"""Web Research orchestration over Net-Syphon's discovered MCP capabilities."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from oris.search import (
    SearchProviderError,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)

TOOL_NAMES = ("net_syphon_search_web", "net_syphon_get_pages")
MAX_RESEARCH_PAGES = 5
"""How many pages one research run reads, which is Net-Syphon's batch maximum.

Three at first, matching what a snippet-based provider gave. Five since the
evaluation on 2026-09-07, which asked for the newest Python 3.12 release and got
back the version with a note that the evidence held no publication date. Three
official pages were read and none happened to carry a date, so the fix at the
time was to read more of them.

That is no longer why five is here. A date now reaches the model two other ways:
Net-Syphon resolves publication dates on news search, and the Web Research
prompt accepts a date printed in a page it actually read. Five stays because a
survey question is better served by five sources than by three, which is an
ordinary breadth judgement rather than a workaround.
"""
MAX_CONTEXT_CHARACTERS_PER_PAGE = 20000
"""How much of one page reaches the model, and what ORIS asks Net-Syphon for.

Sent as `max_characters` on every retrieval batch rather than left to
Net-Syphon's default, so this constant is the request instead of a prediction of
what the server would have done anyway.

It used to be a prediction, and a fragile one. Net-Syphon divided a 40,000
character batch budget by the number of URLs requested, so ORIS held 8,000 to
match what five URLs would produce, and the two numbers had to be kept in step by
hand. Net-Syphon dropped that division on 2026-09-08 and takes a per-page
allowance from the caller instead, bounded at 50,000.

Why 20,000 and not the 50,000 ceiling: the batch total is the real budget, not
the per-page number, and the machine runs out of memory before it runs out of
context window. Measured 2026-09-08 on the deployed Qwen3.5-35B-A3B.

Five pages at 20,000 is 18,551 input tokens and 58 seconds of synthesis. Five at
50,000 is 44,867 tokens, and oMLX's prefill guard refuses it: the weights hold
about 21.9 GB, the prompt's KV and attention working set needs another 1.9 GB,
and the guard's ceiling is 23.6 GB. It succeeded once and was rejected outright
on the next attempt, which makes it not an operating point but a coin toss. The
usable limit is roughly 40,000 input tokens, so the batch ORIS sends leaves
about half the headroom spare.

The 262,144 token context window is not the constraint and never was. It has
room for six times what the memory guard will accept.

Reading one page deeply stays available by asking for fewer URLs at a higher
allowance -- two at 50,000 is the same token count as five at 20,000, and
finished within four seconds of it.
"""


async def load_web_research_tools(python_executable: Path) -> tuple[BaseTool, ...]:
    """Discover only this specialist's tools through the existing official adapter."""
    if not python_executable.is_absolute():
        raise ValueError("NET_SYPHON_PYTHON_EXECUTABLE must be an absolute path")
    if not python_executable.is_file():
        raise FileNotFoundError(
            f"Net-Syphon interpreter not found: {python_executable}"
        )
    client = MultiServerMCPClient(
        {
            "net_syphon": {
                "transport": "stdio",
                "command": str(python_executable),
                "args": ["-m", "net_syphon"],
                # SDK minimal environment only; provider keys live in Net-Syphon's dotenv.
                "env": {},
                # Backstop above the server's 180-second batch deadline.
                "session_kwargs": {"read_timeout_seconds": timedelta(seconds=210)},
            }
        },
        handle_tool_errors=False,
    )
    available = {
        tool.name: tool for tool in await client.get_tools(server_name="net_syphon")
    }
    missing = set(TOOL_NAMES) - available.keys()
    if missing:
        raise RuntimeError(
            f"Net-Syphon is missing required tools: {', '.join(sorted(missing))}"
        )
    return tuple(available[name] for name in TOOL_NAMES)


def _describe_error(error: ToolException) -> str:
    """Recover Net-Syphon's own classification from the adapter's error text.

    Net-Syphon reports a failure as an MCP error carrying a structured code. The
    adapter raises before it builds the structured artifact, so the code and the
    retryable flag survive only inside the exception message.
    """
    try:
        payload = json.loads(str(error))
        code, message = payload["code"], payload["message"]
    except (ValueError, TypeError, KeyError):
        return f"Net-Syphon failed: {error}"
    retryable = "retryable" if payload.get("retryable") else "not retryable"
    return f"Net-Syphon returned {code} ({retryable}): {message}"


async def _call(tool: BaseTool, arguments: dict) -> dict:
    """Read the official adapter's structured artifact, not its display text."""
    try:
        result = await tool.ainvoke(
            {
                "name": tool.name,
                "args": arguments,
                "id": str(uuid4()),
                "type": "tool_call",
            }
        )
    except ToolException as error:
        raise SearchProviderError(_describe_error(error)) from error
    if not isinstance(result, ToolMessage) or not isinstance(result.artifact, dict):
        raise SearchProviderError("Net-Syphon returned no structured result")
    content = result.artifact.get("structured_content")
    if not isinstance(content, dict):
        raise SearchProviderError("Net-Syphon returned no structured result")
    return content


class NetSyphonWebSearch:
    """One search and one bounded retrieval batch, with no model-driven tool loop."""

    def __init__(self, python_executable: Path | None) -> None:
        self.python_executable = python_executable
        self._tools: tuple[BaseTool, ...] | None = None

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        if self.python_executable is None:
            raise SearchProviderError(
                "NET_SYPHON_PYTHON_EXECUTABLE is required for Web Research"
            )
        if self._tools is None:
            self._tools = await load_web_research_tools(self.python_executable)
        search_tool, pages_tool = self._tools
        found = await _call(
            search_tool, request.model_dump(mode="json", exclude_defaults=True)
        )
        selected = found["results"][:MAX_RESEARCH_PAGES]
        if not selected:
            raise SearchProviderError("No search results were available for retrieval")
        pages = await _call(
            pages_tool,
            {
                "urls": [item["url"] for item in selected],
                "max_characters": MAX_CONTEXT_CHARACTERS_PER_PAGE,
            },
        )
        sources = []
        for outcome in pages["results"]:
            page = outcome["page"]
            if page is None:
                continue
            item = selected[outcome["index"] - 1]
            sources.append(
                WebSearchResult(
                    title=item["title"],
                    url=item["url"],
                    snippet=item.get("snippet") or "",
                    published_at=item.get("published_at"),
                    # Net-Syphon was asked to cut at this length and reports
                    # whether it had to, so its flag is the answer. The slice
                    # and the comparison stay as a backstop for the one case
                    # its flag cannot cover: a server that returns more than the
                    # caller asked for has not truncated anything, and ORIS
                    # would silently overspend its prompt budget.
                    content=page["text"][:MAX_CONTEXT_CHARACTERS_PER_PAGE],
                    final_url=page["final_url"],
                    retrieved_at=page["retrieved_at"],
                    truncated=page["truncated"]
                    or len(page["text"]) > MAX_CONTEXT_CHARACTERS_PER_PAGE,
                )
            )
        if not sources:
            raise SearchProviderError(
                "No pages could be retrieved; inspect Net-Syphon's audit logs"
            )
        return WebSearchResponse(
            query=request.query,
            results=tuple(sources),
            provider="net_syphon",
            provider_request_id=found["call_id"],
            partial=found["partial"] or pages["partial"],
        )
