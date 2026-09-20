"""Tests for choosing which search results are worth reading."""

from unittest.mock import Mock

import pytest

from oris.search import WebSearchResult
from oris.source_selection import SourceSelection, select_sources


def candidates(count: int) -> tuple[WebSearchResult, ...]:
    """Build a numbered candidate list with previews and no page content."""
    return tuple(
        WebSearchResult(
            title=f"Source {number}",
            url=f"https://example.org/{number}",
            snippet=f"Preview of {number}",
        )
        for number in range(1, count + 1)
    )


def model_choosing(*numbers: int) -> Mock:
    """A model double that returns one selection by position."""
    model = Mock()
    structured = Mock()
    structured.invoke.return_value = SourceSelection(chosen=numbers)
    model.with_structured_output.return_value = structured
    return model


def test_only_the_chosen_candidates_come_back() -> None:
    """The positions the model returns decide which pages get read."""
    model = model_choosing(7, 2)

    chosen = select_sources(model, "a question", candidates(10), limit=5)

    assert [result.title for result in chosen] == ["Source 7", "Source 2"]


def test_a_short_list_is_taken_whole_without_asking() -> None:
    """Nothing is chosen when there is nothing to choose between.

    Fewer candidates than the run can read means every one of them is being
    read regardless, so the call would cost a round trip to change nothing.
    """
    model = model_choosing(1)

    chosen = select_sources(model, "a question", candidates(4), limit=5)

    assert len(chosen) == 4
    model.with_structured_output.assert_not_called()


def test_more_than_the_limit_is_cut_to_the_limit() -> None:
    """The reading budget is the caller's, not the model's to exceed."""
    model = model_choosing(9, 8, 7, 6, 5, 4, 3)

    chosen = select_sources(model, "a question", candidates(10), limit=5)

    assert len(chosen) == 5
    assert [result.title for result in chosen] == [
        f"Source {number}" for number in (9, 8, 7, 6, 5)
    ]


def test_a_repeated_position_is_not_read_twice() -> None:
    """One page named twice is still one page."""
    model = model_choosing(3, 3, 6)

    chosen = select_sources(model, "a question", candidates(10), limit=5)

    assert [result.title for result in chosen] == ["Source 3", "Source 6"]


def test_positions_outside_the_list_are_dropped() -> None:
    """A number nobody offered costs one candidate, never the whole run."""
    model = model_choosing(99, 4)

    chosen = select_sources(model, "a question", candidates(10), limit=5)

    assert [result.title for result in chosen] == ["Source 4"]


def test_nothing_usable_falls_back_to_the_order_the_engine_gave() -> None:
    """The fallback is exactly what happened before anything chose."""
    model = model_choosing(41, 42, 43)

    chosen = select_sources(model, "a question", candidates(10), limit=5)

    assert [result.title for result in chosen] == [
        f"Source {number}" for number in (1, 2, 3, 4, 5)
    ]


def test_a_wrong_result_type_is_not_guessed_at() -> None:
    """A model that answers with something else is a fault, not a selection."""
    model = Mock()
    structured = Mock()
    structured.invoke.return_value = "Source 1 looks good"
    model.with_structured_output.return_value = structured

    with pytest.raises(TypeError, match="invalid result type"):
        select_sources(model, "a question", candidates(10), limit=5)
