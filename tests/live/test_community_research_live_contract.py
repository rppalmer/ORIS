"""Opt-in end-to-end contract for the Community Research graph."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oris.community_research import create_community_research_graph
from oris.config import Settings
from oris.model import create_chat_model
from oris.net_razor import load_community_research_tools

LIVE_COMMUNITY_RESEARCH_ENABLED = (
    os.environ.get("ORIS_RUN_LIVE_COMMUNITY_RESEARCH_TESTS") == "1"
)
REPORT_DIRECTORY = Path(__file__).parents[2] / "artifacts" / "evaluations"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_COMMUNITY_RESEARCH_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_COMMUNITY_RESEARCH_TESTS=1 to contact "
        "Net-Razor, X, Hacker News, and oMLX."
    ),
)
def test_community_research_completes_one_bounded_live_request() -> None:
    """The configured MCP server and oMLX model satisfy the graph contract."""

    async def run_graph() -> tuple[Settings, dict[str, object], dict[str, object]]:
        settings = Settings()
        if settings.net_razor_python_executable is None:
            pytest.fail(
                "NET_RAZOR_PYTHON_EXECUTABLE is required for this live contract"
            )

        tools = await load_community_research_tools(
            settings.net_razor_python_executable
        )
        graph = create_community_research_graph(
            tools[0],
            create_chat_model(settings),
        )
        request: dict[str, object] = {
            "topic": "LangGraph",
            "days": 30,
            "sources": ["x", "hn"],
            "max_results_per_source": 3,
        }
        result = await graph.ainvoke(request)
        return settings, request, result

    settings, request, result = asyncio.run(run_graph())

    assert set(result) == {"answer", "cited_urls", "research_result"}
    assert isinstance(result["answer"], str)
    assert result["answer"]
    assert isinstance(result["cited_urls"], list)
    research_result = result["research_result"]
    assert isinstance(research_result, dict)
    assert research_result["call_id"]
    assert set(research_result["results"]) == {"x", "hn"}

    generated_at = datetime.now(UTC)
    report = {
        "generated_at": generated_at.isoformat(),
        "model": settings.local_llm_model,
        "request": request,
        "result": result,
    }
    REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORT_DIRECTORY / f"community-research-{timestamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Community Research report: {report_path}")
