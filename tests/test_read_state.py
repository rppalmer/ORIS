"""Tests for the durable record of what ORIS has already read."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oris.read_state import ProcessedItem, ProcessedItemStore


def store(tmp_path: Path) -> ProcessedItemStore:
    """Build a store over a throwaway database."""
    return ProcessedItemStore(tmp_path / "nested" / "read_state.sqlite")


def test_constructing_a_store_touches_no_disk(tmp_path: Path) -> None:
    """The composition root builds one at import, so it must be free."""
    path = tmp_path / "nested" / "read_state.sqlite"

    ProcessedItemStore(path)

    assert not path.exists()
    assert not path.parent.exists()


def test_nothing_is_read_until_it_is_recorded(tmp_path: Path) -> None:
    """An empty store must not hide anything."""
    repository = store(tmp_path)

    assert repository.unread("podcast", ("a", "b", "c")) == ("a", "b", "c")


def test_recorded_items_drop_out_and_the_rest_keep_their_order(
    tmp_path: Path,
) -> None:
    """Order survives the filter because the caller budgets on the result.

    A listing arrives newest first and the run takes the first few. Returning
    a set would make which episodes a run picks depend on hash ordering.
    """
    repository = store(tmp_path)
    repository.record([ProcessedItem(source="podcast", item_id="b")])

    assert repository.unread("podcast", ("a", "b", "c", "d")) == ("a", "c", "d")


def test_read_state_is_kept_per_source(tmp_path: Path) -> None:
    """A podcast and a blog post may share an identifier without colliding."""
    repository = store(tmp_path)
    repository.record([ProcessedItem(source="podcast", item_id="shared")])

    assert repository.unread("podcast", ("shared",)) == ()


def test_recording_the_same_item_twice_is_not_an_error(tmp_path: Path) -> None:
    """A run that crashed after writing some of its episodes must be repeatable.

    The whole failure direction here is that an item appears again rather than
    vanishing unread, which only works if writing it again is safe.
    """
    repository = store(tmp_path)
    item = ProcessedItem(source="podcast", item_id="episode-1", call_id="call-1")

    assert repository.record([item]) == 1
    assert repository.record([item]) == 0
    assert repository.count("podcast") == 1


def test_recording_nothing_writes_nothing(tmp_path: Path) -> None:
    """A recap records nothing, and that path must not create a row."""
    repository = store(tmp_path)

    assert repository.record([]) == 0
    assert repository.count("podcast") == 0


def test_an_item_needs_an_identifier(tmp_path: Path) -> None:
    """An empty identifier would silently match nothing and hide nothing."""
    with pytest.raises(ValidationError):
        ProcessedItem(source="podcast", item_id="")


def test_read_state_survives_a_new_store_over_the_same_file(
    tmp_path: Path,
) -> None:
    """Durability is the entire point: a scheduled run is a fresh process."""
    path = tmp_path / "read_state.sqlite"
    ProcessedItemStore(path).record(
        [ProcessedItem(source="podcast", item_id="episode-1")]
    )

    assert ProcessedItemStore(path).unread("podcast", ("episode-1", "episode-2")) == (
        "episode-2",
    )
