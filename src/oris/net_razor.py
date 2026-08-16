"""Official MCP connection for the local Net-Razor capability provider."""

from datetime import timedelta
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

NET_RAZOR_SERVER_NAME = "net_razor"

# Without a session read timeout the MCP SDK skips its `anyio.fail_after` guard
# entirely and a request waits forever. Longer than ThreatSyft's because a
# transcript fetch depends on YouTube rather than on a bounded provider fan-out.
READ_TIMEOUT = timedelta(seconds=120)
COMMUNITY_RESEARCH_TOOL_NAMES = ("net_razor_research",)
YOUTUBE_CATCH_UP_TOOL_NAMES = (
    "net_razor_yt_new_videos",
    "net_razor_yt_transcript",
    "net_razor_yt_mark_processed",
)


def create_net_razor_client(python_executable: Path) -> MultiServerMCPClient:
    """Configure the official stateless stdio client for Net-Razor."""
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
                "session_kwargs": {"read_timeout_seconds": READ_TIMEOUT},
            }
        },
        handle_tool_errors=False,
    )


async def _load_tools(
    python_executable: Path,
    tool_names: tuple[str, ...],
) -> tuple[BaseTool, ...]:
    """Load an ordered allowlist from the official MCP adapter."""
    client = create_net_razor_client(python_executable)
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


async def load_youtube_catch_up_tools(
    python_executable: Path,
) -> tuple[BaseTool, ...]:
    """Load only the MCP tools approved for YouTube Catch-up."""
    return await _load_tools(python_executable, YOUTUBE_CATCH_UP_TOOL_NAMES)
