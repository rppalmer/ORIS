"""Tests for bounded model-generated search plans."""

from datetime import date
from unittest.mock import Mock

import pytest
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from oris.search_planning import SearchPlan, create_search_plan


def test_search_plan_enforces_bounded_output() -> None:
    """A plan accepts only the small provider-independent contract."""
    plan = SearchPlan(
        search_query="latest Python 3.12 release date",
        include_domains=("python.org",),
        time_range="year",
    )

    assert plan.search_query == "latest Python 3.12 release date"
    assert plan.include_domains == ("python.org",)
    assert plan.time_range == "year"


@pytest.mark.parametrize(
    ("values"),
    [
        {"search_query": "   "},
        {
            "search_query": "Python",
            "include_domains": ("one.test", "two.test", "three.test", "four.test"),
        },
        {"search_query": "Python", "include_domains": ("https://python.org",)},
        {"search_query": "Python", "time_range": "decade"},
        {"search_query": "Python", "search_category": "videos"},
        {"search_query": "Python", "start_date": "2026-08-07"},
        {
            "search_query": "Python",
            "start_date": "2026-08-08",
            "end_date": "2026-08-07",
        },
        {
            "search_query": "Python",
            "time_range": "day",
            "start_date": "2026-08-07",
            "end_date": "2026-08-08",
        },
        {"search_query": "Python", "provider": "tavily"},
    ],
)
def test_search_plan_rejects_invalid_output(values: dict[str, object]) -> None:
    """Invalid or provider-specific plan output fails validation."""
    with pytest.raises(ValidationError):
        SearchPlan.model_validate(values)


def test_create_search_plan_uses_one_structured_model_call() -> None:
    """Planning is one bounded model call and does not execute a search."""
    expected_plan = SearchPlan(
        search_query="SQLite WAL concurrent readers single writer limitations",
    )
    structured_model = Mock()
    structured_model.invoke.return_value = expected_plan
    model = Mock(spec=BaseChatModel)
    model.with_structured_output.return_value = structured_model

    plan = create_search_plan(
        model,
        "  How does SQLite WAL affect readers and writers?  ",
        current_date=date(2026, 7, 29),
    )

    assert plan == expected_plan
    assert plan.search_category == "general"
    model.with_structured_output.assert_called_once_with(
        SearchPlan,
        method="json_schema",
    )
    structured_model.invoke.assert_called_once()
    messages = structured_model.invoke.call_args.args[0]
    assert messages[0][0] == "system"
    assert "Do not answer the question" in messages[0][1]
    assert messages[1] == (
        "human",
        "Current date: 2026-07-29\n"
        "Research question: How does SQLite WAL affect readers and writers?",
    )
    assert structured_model.invoke.call_args.kwargs == {"max_completion_tokens": 256}


def test_create_search_plan_rejects_blank_input_before_model_call() -> None:
    """A blank question fails before planning invokes the model."""
    model = Mock(spec=BaseChatModel)

    with pytest.raises(ValidationError):
        create_search_plan(model, "   ", current_date=date(2026, 7, 29))

    model.with_structured_output.assert_not_called()
