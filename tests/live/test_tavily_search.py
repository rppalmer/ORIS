"""Opt-in contract test for the configured Tavily search integration."""

import asyncio
import os

import pytest

from oris.config import Settings
from oris.search import WebSearchRequest, WebSearchResponse
from oris.tavily import TavilyWebSearch, create_tavily_search

LIVE_TAVILY_TESTS_ENABLED = os.environ.get("ORIS_RUN_LIVE_TAVILY_TESTS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_TAVILY_TESTS_ENABLED,
    reason=("Set ORIS_RUN_LIVE_TAVILY_TESTS=1 to spend one Tavily search credit."),
)
def test_tavily_search_returns_bounded_source_results() -> None:
    """Tavily satisfies the response contract needed by web research."""
    settings = Settings()
    search = TavilyWebSearch(
        create_tavily_search(settings),
        create_tavily_search(settings, topic="news"),
    )

    response = asyncio.run(
        search.search(
            WebSearchRequest(
                query="What is the LangGraph Python framework?",
                include_domains=("docs.langchain.com",),
            )
        )
    )

    assert isinstance(response, WebSearchResponse)
    assert response.query
    assert response.provider == "tavily"
    assert response.provider_request_id
    assert 1 <= len(response.results) <= 5
    for result in response.results:
        assert result.title
        assert result.url
        assert result.url.host == "docs.langchain.com"
        assert result.snippet
        assert isinstance(result.relevance_score, int | float)
