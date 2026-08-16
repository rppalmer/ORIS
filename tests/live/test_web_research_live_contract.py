"""Opt-in end-to-end contract for the Web Research graph."""

import asyncio
import os

import pytest

from oris.config import Settings
from oris.model import create_chat_model
from oris.tavily import TavilyWebSearch, create_tavily_search
from oris.web_research import CitedAnswer, create_web_research_graph

LIVE_WEB_RESEARCH_ENABLED = os.environ.get("ORIS_RUN_LIVE_WEB_RESEARCH_TESTS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_WEB_RESEARCH_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_WEB_RESEARCH_TESTS=1 to contact oMLX "
        "and spend one Tavily search credit."
    ),
)
def test_web_research_returns_a_cited_answer() -> None:
    """The configured Tavily and oMLX services satisfy the complete workflow."""
    settings = Settings()
    search = TavilyWebSearch(
        create_tavily_search(settings),
        create_tavily_search(settings, topic="news"),
    )
    model = create_chat_model(settings)
    graph = create_web_research_graph(search, model)

    result = asyncio.run(
        graph.ainvoke({"query": "What is the LangGraph Python framework?"})
    )

    assert set(result) == {"answer", "sources"}
    assert isinstance(result["answer"], CitedAnswer)
    assert result["answer"].answer
    assert 1 <= len(result["sources"]) <= 5
