"""Tests for the Tavily web-search implementation."""

from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from langchain_core.tools import ToolException
from langchain_tavily import TavilySearch

from oris.config import Settings
from oris.search import SearchProviderError, WebSearchRequest
from oris.tavily import TavilyWebSearch, create_tavily_search

TEST_SETTINGS = {
    "LOCAL_LLM_BASE_URL": "http://llm.test/v1",
    "LOCAL_LLM_MODEL": "local-test-model",
    "LOCAL_LLM_API_KEY": "local-test-key",
    "TAVILY_API_KEY": "tavily-test-key",
    "LANGSMITH_TRACING": False,
}


def test_create_tavily_search_uses_fixed_conservative_settings() -> None:
    """The factory fixes search scope, cost, and optional response content."""
    settings = Settings(_env_file=None, **TEST_SETTINGS)

    search = create_tavily_search(settings)
    news_search = create_tavily_search(settings, topic="news")

    assert search.max_results == 5
    assert search.topic == "general"
    assert search.search_depth == "basic"
    assert search.auto_parameters is False
    assert search.include_answer is False
    assert search.include_raw_content is False
    assert search.include_images is False
    assert search.include_image_descriptions is False
    assert search.include_favicon is False
    assert search.include_usage is False
    assert search.api_wrapper.tavily_api_key.get_secret_value() == "tavily-test-key"
    assert search.handle_tool_error is False
    assert news_search.topic == "news"


def test_tavily_web_search_reports_the_provider_reason_for_no_results() -> None:
    """An empty result set reports Tavily's reason, not a response-shape error."""
    tool = Mock(spec=TavilySearch)
    tool.invoke.side_effect = ToolException(
        "No search results found for 'obscure query'. Suggestions: broaden the query."
    )
    search = TavilyWebSearch(general_search=tool, news_search=tool)

    with pytest.raises(SearchProviderError, match="No search results found"):
        search.search(WebSearchRequest(query="obscure query"))


def test_tavily_web_search_normalizes_provider_response() -> None:
    """The adapter exposes stable evidence instead of Tavily's raw payload."""
    tool = Mock(spec=TavilySearch)
    tool.invoke.return_value = {
        "query": "LangGraph persistence",
        "answer": "Provider-generated answer that must not cross the boundary.",
        "images": ["unused-image"],
        "results": [
            {
                "title": "Persistence",
                "url": "https://docs.langchain.com/oss/python/langgraph/persistence",
                "content": "LangGraph has a built-in persistence layer.",
                "score": 0.91,
                "raw_content": "Raw page content that must not cross the boundary.",
            }
        ],
        "response_time": "0.42",
        "request_id": "request-123",
    }
    news_tool = Mock(spec=TavilySearch)
    search = TavilyWebSearch(tool, news_tool)

    response = search.search(WebSearchRequest(query="LangGraph persistence"))

    tool.invoke.assert_called_once_with({"query": "LangGraph persistence"})
    assert response.model_dump(mode="json") == {
        "query": "LangGraph persistence",
        "results": [
            {
                "title": "Persistence",
                "url": "https://docs.langchain.com/oss/python/langgraph/persistence",
                "snippet": "LangGraph has a built-in persistence layer.",
                "relevance_score": 0.91,
                "published_at": None,
            }
        ],
        "provider": "tavily",
        "provider_request_id": "request-123",
        "provider_response_time_seconds": 0.42,
    }


def test_tavily_web_search_passes_explicit_search_controls() -> None:
    """The adapter passes supported controls to the official integration."""
    tool = Mock(spec=TavilySearch)
    tool.invoke.return_value = {
        "query": "Latest Python 3.12 release",
        "results": [
            {
                "title": "Python 3.12.13",
                "url": "https://python.org/downloads/release/python-31213",
                "content": "Python 3.12.13 was released March 3, 2026.",
                "score": 0.9,
            }
        ],
    }
    news_tool = Mock(spec=TavilySearch)
    search = TavilyWebSearch(tool, news_tool)

    search.search(
        WebSearchRequest(
            query="Latest Python 3.12 release",
            include_domains=("python.org",),
            time_range="year",
        )
    )

    tool.invoke.assert_called_once_with(
        {
            "query": "Latest Python 3.12 release",
            "include_domains": ["python.org"],
            "time_range": "year",
        }
    )
    news_tool.invoke.assert_not_called()


def test_tavily_web_search_uses_news_dates_and_normalizes_publication_time() -> None:
    """Exact news controls select the news tool and retain publication time."""
    general_tool = Mock(spec=TavilySearch)
    news_tool = Mock(spec=TavilySearch)
    news_tool.invoke.return_value = {
        "query": "AI-agent developments 2026-08-07",
        "results": [
            {
                "title": "Out-of-range update",
                "url": "https://example.com/out-of-range",
                "content": "This result belongs to the following day.",
                "score": 0.95,
                "published_date": "Sat, 08 Aug 2026 08:35:45 GMT",
            },
            {
                "title": "Agent security changes",
                "url": "https://example.com/agent-security",
                "content": "A dated AI-agent development.",
                "score": 0.9,
                "published_date": "Fri, 07 Aug 2026 17:46:20 GMT",
            },
        ],
    }
    search = TavilyWebSearch(general_tool, news_tool)

    response = search.search(
        WebSearchRequest(
            query="AI-agent developments 2026-08-07",
            search_category="news",
            start_date=date(2026, 8, 7),
            end_date=date(2026, 8, 8),
        )
    )

    general_tool.invoke.assert_not_called()
    news_tool.invoke.assert_called_once_with(
        {
            "query": "AI-agent developments 2026-08-07",
            "start_date": "2026-08-07",
            "end_date": "2026-08-08",
        }
    )
    assert response.results[0].published_at == datetime(
        2026, 8, 7, 17, 46, 20, tzinfo=UTC
    )
    assert len(response.results) == 1


def test_tavily_web_search_maps_provider_errors() -> None:
    """Provider failures cross the boundary as one application error type."""
    tool = Mock(spec=TavilySearch)
    provider_error = TimeoutError("provider timed out")
    tool.invoke.return_value = {"error": provider_error}
    search = TavilyWebSearch(tool, Mock(spec=TavilySearch))

    with pytest.raises(SearchProviderError, match="Tavily search failed") as error:
        search.search(WebSearchRequest(query="LangGraph persistence"))

    assert error.value.__cause__ is provider_error
