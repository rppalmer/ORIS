"""Tests for application-owned web-search contracts."""

from datetime import date

import pytest
from pydantic import ValidationError

from oris.search import WebSearchRequest, WebSearchResult


def test_web_search_request_strips_surrounding_whitespace() -> None:
    """The normalized query is the exact query sent to a provider."""
    request = WebSearchRequest(query="  LangGraph persistence  ")

    assert request.query == "LangGraph persistence"
    assert request.include_domains == ()
    assert request.search_category == "general"
    assert request.time_range is None
    assert request.start_date is None
    assert request.end_date is None


def test_web_search_request_rejects_blank_queries() -> None:
    """An invalid request fails before any provider can be called."""
    with pytest.raises(ValidationError):
        WebSearchRequest(query="   ")


def test_web_search_request_validates_explicit_search_controls() -> None:
    """Domain and recency controls are bounded and normalized."""
    request = WebSearchRequest(
        query="Latest Python 3.12 release",
        include_domains=("  python.org  ",),
        time_range="year",
    )

    assert request.include_domains == ("python.org",)
    assert request.time_range == "year"

    exact_date_request = WebSearchRequest(
        query="AI-agent developments",
        search_category="news",
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 8),
    )
    assert exact_date_request.search_category == "news"
    assert exact_date_request.start_date == date(2026, 8, 7)
    assert exact_date_request.end_date == date(2026, 8, 8)


def test_web_search_request_rejects_invalid_search_controls() -> None:
    """Invalid domains and provider-unsupported time ranges fail locally."""
    with pytest.raises(ValidationError):
        WebSearchRequest(query="Python", include_domains=("   ",))

    with pytest.raises(ValidationError):
        WebSearchRequest(query="Python", include_domains=("https://python.org",))

    with pytest.raises(ValidationError):
        WebSearchRequest(query="Python", time_range="decade")

    with pytest.raises(ValidationError, match="provided together"):
        WebSearchRequest(query="Python", start_date=date(2026, 8, 7))

    with pytest.raises(ValidationError, match="earlier"):
        WebSearchRequest(
            query="Python",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 7),
        )

    with pytest.raises(ValidationError, match="cannot be combined"):
        WebSearchRequest(
            query="Python",
            time_range="day",
            start_date=date(2026, 8, 7),
            end_date=date(2026, 8, 8),
        )


def test_web_search_result_serializes_url_as_a_string() -> None:
    """Agent Server can return a source URL instead of serializing it as null."""
    result = WebSearchResult(
        title="LangGraph overview",
        url="https://docs.langchain.com/oss/python/langgraph/overview",
        snippet="LangGraph supports stateful agent workflows.",
        relevance_score=0.95,
    )

    assert result.model_dump()["url"] == (
        "https://docs.langchain.com/oss/python/langgraph/overview"
    )
