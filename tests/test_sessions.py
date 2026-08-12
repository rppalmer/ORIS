"""Tests for the active conversation-session pointer."""

from uuid import UUID

import pytest

from oris.sessions import load_or_create_session, start_new_session


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
