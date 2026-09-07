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

Three at first, matching what a snippet-based provider gave. The evaluation on
2026-09-07 showed the cost: asked for the newest Python 3.12 release, the
answer named the version and said the evidence held no publication date. Three
official pages were retrieved and none of them happened to carry it. Search
results arrive with `published_at` unset, so a date only reaches the model if
it appears in a page that was actually read, and reading more pages is the
only lever ORIS has over that. Five is the server's own per-batch limit, and
40,000 characters is its per-batch character budget, which this matches at
8,000 a page.
"""
MAX_CONTEXT_CHARACTERS_PER_PAGE = 8000


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
        pages = await _call(pages_tool, {"urls": [item["url"] for item in selected]})
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
