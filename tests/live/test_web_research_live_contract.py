"""Opt-in end-to-end contract for the Web Research graph."""

import asyncio
import os

import pytest

from oris.config import Settings
from oris.model import create_chat_model
from oris.net_syphon import NetSyphonWebSearch
from oris.web_research import CitedAnswer, create_web_research_graph

LIVE_WEB_RESEARCH_ENABLED = os.environ.get("ORIS_RUN_LIVE_WEB_RESEARCH_TESTS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_WEB_RESEARCH_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_WEB_RESEARCH_TESTS=1 to contact oMLX "
        "and use configured Net-Syphon search/retrieval credits."
    ),
)
def test_web_research_returns_a_cited_answer() -> None:
    """Configured MCP retrieval and the model complete the fixed research path."""
    settings = Settings()
    search = NetSyphonWebSearch(settings.net_syphon_python_executable)
    model = create_chat_model(settings)
    graph = create_web_research_graph(search, model)

    result = asyncio.run(
        graph.ainvoke({"query": "What is the LangGraph Python framework?"})
    )

    assert set(result) == {"answer", "sources"}
    assert isinstance(result["answer"], CitedAnswer)
    assert result["answer"].answer
    assert 1 <= len(result["sources"]) <= 3
    assert all(source.content for source in result["sources"])
