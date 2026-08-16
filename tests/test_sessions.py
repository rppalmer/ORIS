"""Tests for the active conversation-session pointer and the session list."""

from uuid import UUID

import pytest
from checkpoint_fixture import write_session

from oris.sessions import (
    MAX_TITLE_LENGTH,
    delete_session,
    list_sessions,
    load_or_create_session,
    session_transcript,
    start_new_session,
)


def test_session_id_survives_application_restart(tmp_path) -> None:
    """Loading the same pointer resumes the existing UUID session."""
    session_file_path = tmp_path / "current_session"

    first_session_id = load_or_create_session(session_file_path)
    resumed_session_id = load_or_create_session(session_file_path)

    assert UUID(first_session_id)
    assert resumed_session_id == first_session_id
    assert session_file_path.read_text(encoding="utf-8").strip() == first_session_id


def test_start_new_session_replaces_the_active_pointer(tmp_path) -> None:
    """Starting a session changes context without deleting the earlier thread."""
    session_file_path = tmp_path / "current_session"
    earlier_session_id = load_or_create_session(session_file_path)

    new_session_id = start_new_session(session_file_path)

    assert UUID(new_session_id)
    assert new_session_id != earlier_session_id
    assert session_file_path.read_text(encoding="utf-8").strip() == new_session_id


def test_load_session_rejects_an_invalid_pointer(tmp_path) -> None:
    """A damaged pointer fails clearly instead of selecting the wrong history."""
    session_file_path = tmp_path / "current_session"
    session_file_path.write_text("not-a-session-id\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid active session file"):
        load_or_create_session(session_file_path)


def test_no_checkpoint_database_lists_nothing(tmp_path) -> None:
    """A first run has no history, which is not an error."""
    assert list_sessions(tmp_path / "checkpoints.sqlite") == []
    assert session_transcript(tmp_path / "checkpoints.sqlite", "any") == []


def test_sessions_are_listed_most_recently_active_first(tmp_path) -> None:
    """A session picker is only useful if the one just used is at the top."""
    database_path = tmp_path / "checkpoints.sqlite"
    write_session(database_path, "older", [("what is a canary token", "…")])
    write_session(
        database_path,
        "newer",
        [("enrich 8.8.8.8", "…"), ("which service is oldest", "…")],
    )

    listed = list_sessions(database_path)

    assert [summary.thread_id for summary in listed] == ["newer", "older"]
    # The most recent request, not the first: a long session moves on, and the
    # title has to agree with the time shown beside it.
    assert listed[0].title == "which service is oldest"
    assert listed[0].turns == 2
    assert listed[0].last_active is not None


def test_a_long_first_request_is_shortened_into_a_title(tmp_path) -> None:
    """A session list has one narrow column; an unbounded title destroys it."""
    database_path = tmp_path / "checkpoints.sqlite"
    write_session(database_path, "verbose", [("word " * 40, "…")])

    title = list_sessions(database_path)[0].title

    assert len(title) <= MAX_TITLE_LENGTH
    assert title.endswith("…")


def test_a_transcript_reads_back_in_order(tmp_path) -> None:
    """Switching sessions must show what the next turn will continue from."""
    database_path = tmp_path / "checkpoints.sqlite"
    write_session(
        database_path, "thread", [("first", "answer one"), ("second", "answer two")]
    )

    transcript = session_transcript(database_path, "thread")

    assert transcript == [
        ("you", "first"),
        ("oris", "answer one"),
        ("you", "second"),
        ("oris", "answer two"),
    ]
    assert session_transcript(database_path, "missing") == []


def test_deleting_a_session_removes_only_that_conversation(tmp_path) -> None:
    """The list has to be able to shrink, and the neighbour has to survive."""
    database_path = tmp_path / "checkpoints.sqlite"
    write_session(database_path, "doomed", [("delete me", "…")])
    write_session(database_path, "kept", [("leave me alone", "…")])

    delete_session(database_path, "doomed")

    assert [summary.thread_id for summary in list_sessions(database_path)] == ["kept"]
    assert session_transcript(database_path, "doomed") == []


def test_deleting_from_a_missing_database_is_not_an_error(tmp_path) -> None:
    delete_session(tmp_path / "checkpoints.sqlite", "anything")
