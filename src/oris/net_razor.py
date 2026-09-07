"""Official MCP connection for the local Net-Razor capability provider."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

NET_RAZOR_SERVER_NAME = "net_razor"

# Without a session read timeout the MCP SDK skips its `anyio.fail_after` guard
# entirely and a request waits forever. Longer than ThreatSyft's because a
# transcript fetch reaches a podcast feed and its host rather than a bounded
# provider fan-out.
READ_TIMEOUT = timedelta(seconds=120)

NET_RAZOR_TRANSCRIPTION_CEILING = 1230
"""How long one podcast transcription can legitimately take, in seconds.

Net-Razor bounds the work in three stages that run in sequence: a 30 second
feed fetch to find the audio URL, a 300 second download, and a 900 second
transcriber subprocess. At 1230 it gives up and returns a classified error
saying which stage failed.

Recorded here because ORIS's own deadline is derived from it and has no meaning
without it. Net-Razor treats a change to any of the three as a contract change.
"""

WHISPER_READ_TIMEOUT = timedelta(seconds=1380)
"""The deadline for podcast transcription alone, on the scheduled path.

Deliberately above `NET_RAZOR_TRANSCRIPTION_CEILING` so it never fires. Net-Razor
owns the real limit and describes its own failures; this is a backstop against a
hung session, and if it won the race it would replace a classified error with a
dead connection. The 150 second margin covers subprocess start and MCP framing.
"""
COMMUNITY_RESEARCH_TOOL_NAMES = ("net_razor_research",)
PODCAST_CATCH_UP_TOOL_NAMES = (
    "net_razor_podcast_new_episodes",
    "net_razor_podcast_transcript",
)
"""The tools both podcast graphs hold. Transcription is deliberately not here.

`net_razor_podcast_mark_processed` used to be the third. Net-Razor dropped it
because it tracked read state in one global table with no notion of who had
read what, so any consumer's acknowledgement hid an item from every other one.
ORIS keeps that record itself now; see `oris.read_state`.
"""

PODCAST_FEEDS_TOOL_NAME = "net_razor_podcast_feeds"
"""Loaded separately, because listing shows is not part of catching up.

Kept off the catch-up allowlist so a catch-up cannot call it. It reads every
configured feed, which costs about a second, and a catch-up already learns each
show's name from the episodes it fetches. The question it answers is "what am I
subscribed to", which is asked before a catch-up rather than during one.
"""

PODCAST_TRANSCRIPTION_TOOL_NAME = "net_razor_podcast_whisper_transcript"
"""Loaded separately, for the scheduled graph only.

Separate for two reasons. It needs `WHISPER_READ_TIMEOUT`, which is a property
of the client rather than the call. And a person typing into a chat must not be
able to start work that blocks for minutes, which is guaranteed by the
interactive graph never holding the tool.
"""


def create_net_razor_client(
    python_executable: Path,
    *,
    read_timeout: timedelta = READ_TIMEOUT,
) -> MultiServerMCPClient:
    """Configure the official stateless stdio client for Net-Razor.

    The read timeout belongs to the client rather than to a call: the client is
    stateless, so the official adapter opens a fresh session per tool call and
    never passes a per-call override. Giving one tool a longer deadline
    therefore means building a second client for it alone.
    """
    if not python_executable.is_absolute():
        raise ValueError("NET_RAZOR_PYTHON_EXECUTABLE must be an absolute path")
    if not python_executable.is_file():
        raise FileNotFoundError(
            f"Net-Razor Python executable not found: {python_executable}"
        )

    return MultiServerMCPClient(
        {
            NET_RAZOR_SERVER_NAME: {
                "transport": "stdio",
                "command": str(python_executable),
                "args": ["-m", "net_razor.mcp"],
                "session_kwargs": {"read_timeout_seconds": read_timeout},
            }
        },
        handle_tool_errors=False,
    )


async def _load_tools(
    python_executable: Path,
    tool_names: tuple[str, ...],
    *,
    read_timeout: timedelta = READ_TIMEOUT,
) -> tuple[BaseTool, ...]:
    """Load an ordered allowlist from the official MCP adapter."""
    client = create_net_razor_client(python_executable, read_timeout=read_timeout)
    available_tools = await client.get_tools(server_name=NET_RAZOR_SERVER_NAME)
    tools_by_name = {tool.name: tool for tool in available_tools}
    missing_tools = [name for name in tool_names if name not in tools_by_name]
    if missing_tools:
        raise RuntimeError(
            f"Net-Razor is missing required MCP tools: {', '.join(missing_tools)}"
        )
    return tuple(tools_by_name[name] for name in tool_names)


async def load_community_research_tools(
    python_executable: Path,
) -> tuple[BaseTool, ...]:
    """Load only the MCP tools approved for Community Research."""
    return await _load_tools(python_executable, COMMUNITY_RESEARCH_TOOL_NAMES)


async def load_podcast_catch_up_tools(
    python_executable: Path,
) -> tuple[BaseTool, ...]:
    """Load the podcast tools that answer in seconds."""
    return await _load_tools(python_executable, PODCAST_CATCH_UP_TOOL_NAMES)


async def load_podcast_feeds_tool(python_executable: Path) -> BaseTool:
    """Load the tool that names the configured shows, and nothing else."""
    tools = await _load_tools(python_executable, (PODCAST_FEEDS_TOOL_NAME,))
    return tools[0]


async def load_podcast_transcription_tool(python_executable: Path) -> BaseTool:
    """Load podcast transcription alone, on its own longer deadline.

    Always present: with transcription switched off in Net-Razor the tool still
    exists and returns a `not_configured` error, so its absence is a genuine
    fault rather than a supported configuration.
    """
    tools = await _load_tools(
        python_executable,
        (PODCAST_TRANSCRIPTION_TOOL_NAME,),
        read_timeout=WHISPER_READ_TIMEOUT,
    )
    return tools[0]


class NetRazorError(RuntimeError):
    """A Net-Razor call that failed, carrying Net-Razor's own classification.

    Net-Razor reports a failure two ways. Today most of them arrive as an
    `errors` array inside an otherwise successful result; it is moving them to
    real MCP errors. ORIS holds `handle_tool_errors=False` on the client so the
    adapter raises instead of flattening the failure into display text, and this
    is where that exception becomes something ORIS can act on rather than a
    stack trace about a missing artifact.

    `error_type` is Net-Razor's classification when it published one, so a
    caller can branch on the same names it already reads out of a soft error.
    """

    def __init__(self, message: str, *, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


def _describe_error(error: ToolException) -> NetRazorError:
    """Recover Net-Razor's classification from the adapter's error text.

    The adapter raises before it builds the structured artifact, so the type and
    the message survive only inside the exception string. Net-Razor's soft
    errors carry `type` and `message`, and this reads the same pair back out.
    The exact wire shape of its MCP errors is not settled yet, so anything that
    does not parse is passed through whole rather than discarded.
    """
    try:
        payload = json.loads(str(error))
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return NetRazorError(f"Net-Razor failed: {error}")

    message = payload.get("message")
    if not isinstance(message, str):
        message = str(error)
    error_type = payload.get("type") or payload.get("code")
    if not isinstance(error_type, str):
        return NetRazorError(f"Net-Razor failed: {message}")
    return NetRazorError(
        f"Net-Razor returned {error_type}: {message}",
        error_type=error_type,
    )


async def call_net_razor_tool(
    tool: BaseTool,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Call one Net-Razor tool and return the structured JSON it published.

    Every Net-Razor call in ORIS goes through here, so a failure has one shape
    at the boundary no matter which specialist made the call.
    """
    try:
        result = await tool.ainvoke(
            {
                "type": "tool_call",
                "id": str(uuid4()),
                "name": tool.name,
                "args": arguments,
            }
        )
    except ToolException as error:
        raise _describe_error(error) from error
    if not isinstance(result, ToolMessage):
        raise NetRazorError("Net-Razor did not return a LangChain ToolMessage")
    if not isinstance(result.artifact, dict):
        raise NetRazorError("Net-Razor did not return structured JSON")
    structured_content = result.artifact.get("structured_content")
    if not isinstance(structured_content, dict):
        raise NetRazorError("Net-Razor did not return structured JSON")
    return structured_content
