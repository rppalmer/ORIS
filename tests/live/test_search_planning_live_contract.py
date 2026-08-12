"""Opt-in contract for search planning with the configured local model."""

import os
from datetime import date

import pytest

from oris.config import Settings
from oris.model import create_chat_model
from oris.search_planning import create_search_plan

LIVE_SEARCH_PLANNING_ENABLED = (
    os.environ.get("ORIS_RUN_LIVE_SEARCH_PLANNING_TESTS") == "1"
)


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_SEARCH_PLANNING_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_SEARCH_PLANNING_TESTS=1 to contact the "
        "configured local model without performing a web search."
    ),
)
def test_search_planner_separates_publication_filters_from_current_state() -> None:
    """The local model distinguishes publication recency from current state."""
    model = create_chat_model(Settings())
    current_date = date(2026, 7, 29)

    explicit_plan = create_search_plan(
        model,
        "What LangGraph features were announced in the past week? "
        "Use only docs.langchain.com.",
        current_date=current_date,
    )
    assert explicit_plan.include_domains == ("docs.langchain.com",)
    assert explicit_plan.search_category == "general"
    assert explicit_plan.time_range == "week"
    assert "site:" not in explicit_plan.search_query.lower()

    current_state_plan = create_search_plan(
        model,
        "What is today's weather for ZIP code 48383?",
        current_date=current_date,
    )
    assert current_state_plan.time_range is None
    assert current_state_plan.search_category == "general"
    assert current_state_plan.start_date is None
    assert current_state_plan.end_date is None


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_SEARCH_PLANNING_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_SEARCH_PLANNING_TESTS=1 to contact the "
        "configured local model without performing a web search."
    ),
)
def test_search_planner_selects_news_for_an_explicit_news_request() -> None:
    """The local model selects the existing news search category."""
    news_plan = create_search_plan(
        create_chat_model(Settings()),
        "What important AI-agent news was published yesterday?",
        current_date=date(2026, 7, 29),
    )
    assert news_plan.search_category == "news"
