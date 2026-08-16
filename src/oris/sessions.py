"""ORIS's active conversation pointer, and a read-only view of the rest."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

ACTIVE_SESSION_FILENAME = "current_session"
MAX_TITLE_LENGTH = 48


@dataclass(frozen=True)
class SessionSummary:
    """One conversation thread, described well enough to choose between them."""

    thread_id: str
    title: str
    turns: int
    last_active: datetime | None


def list_sessions(checkpoint_database_path: Path) -> list[SessionSummary]:
    """Return every stored conversation, most recently active first.

    The checkpointer has no API for enumerating threads — `list()` requires a
    thread_id — so the IDs come from SQL and each thread's detail comes from the
    checkpointer itself, rather than this decoding its storage format.
    """
    if not checkpoint_database_path.is_file():
        return []
    try:
        with closing(
            sqlite3.connect(f"file:{checkpoint_database_path}?mode=ro", uri=True)
        ) as con:
            thread_ids = [
                row[0]
                for row in con.execute("SELECT DISTINCT thread_id FROM checkpoints")
            ]
    except sqlite3.Error:
        return []

    summaries: list[SessionSummary] = []
    with SqliteSaver.from_conn_string(str(checkpoint_database_path)) as saver:
        for thread_id in thread_ids:
            latest = saver.get_tuple({"configurable": {"thread_id": thread_id}})
            if latest is None:
                continue
            messages = (latest.checkpoint.get("channel_values") or {}).get(
                "messages"
            ) or []
            requests = [m for m in messages if isinstance(m, HumanMessage)]
            summaries.append(
                SessionSummary(
                    thread_id=thread_id,
                    title=_title(requests),
                    turns=len(requests),
                    last_active=_timestamp(latest.checkpoint.get("ts")),
                )
            )
    # A thread with no readable timestamp sorts last rather than disappearing.
    return sorted(
        summaries,
        key=lambda s: (s.last_active is not None, s.last_active or datetime.min),
        reverse=True,
    )


def session_transcript(
    checkpoint_database_path: Path,
    thread_id: str,
) -> list[tuple[str, str]]:
    """Return one conversation as (role, text) pairs, oldest first.

    Reading history back out of the checkpointer is what makes switching
    sessions in an interface honest: the transcript shown is the state the next
    turn will actually continue from, not a separate log kept alongside it.
    """
    if not checkpoint_database_path.is_file():
        return []
    with SqliteSaver.from_conn_string(str(checkpoint_database_path)) as saver:
        latest = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    if latest is None:
        return []
    messages = (latest.checkpoint.get("channel_values") or {}).get("messages") or []
    transcript = []
    for message in messages:
        role = "you" if isinstance(message, HumanMessage) else "oris"
        text = str(getattr(message, "text", "") or message.content).strip()
        if text:
            transcript.append((role, text))
    return transcript


def delete_session(checkpoint_database_path: Path, thread_id: str) -> None:
    """Remove one conversation's stored state.

    The checkpointer owns its storage layout, and it already knows how to drop a
    thread — checkpoints and pending writes both — so this does not go near the
    tables itself.
    """
    if not checkpoint_database_path.is_file():
        return
    with SqliteSaver.from_conn_string(str(checkpoint_database_path)) as saver:
        saver.delete_thread(thread_id)


def _title(requests: list[HumanMessage]) -> str:
    """Name a session by its most recent request.

    The first request ages badly: a long-running session keeps working on new
    things, and a name from three days ago sitting beside a timestamp from ten
    minutes ago reads as someone else's data. The latest request agrees with the
    time shown next to it.
    """
    if not requests:
        return "(empty)"
    text = " ".join(str(requests[-1].content).split())
    return text[: MAX_TITLE_LENGTH - 1] + "…" if len(text) > MAX_TITLE_LENGTH else text


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _write_active_session_id(path: Path, session_id: str) -> None:
    """Replace the active-session pointer without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(f"{session_id}\n", encoding="utf-8")
    temporary_path.replace(path)


def start_new_session(path: Path) -> str:
    """Create, persist, and return a new unique conversation session ID."""
    session_id = str(uuid4())
    _write_active_session_id(path, session_id)
    return session_id


def set_active_session(path: Path, session_id: str) -> str:
    """Continue an existing conversation, and keep continuing it after a restart.

    Choosing a session from a list is a decision about which conversation is
    current, so it belongs in the same pointer `/new` writes. Otherwise the
    choice silently expires when the process does.
    """
    _write_active_session_id(path, session_id)
    return session_id


def load_or_create_session(path: Path) -> str:
    """Resume the persisted session or create one on first use."""
    if not path.exists():
        return start_new_session(path)

    stored_value = path.read_text(encoding="utf-8").strip()
    try:
        return str(UUID(stored_value))
    except ValueError as error:
        raise ValueError(f"Invalid active session file: {path}") from error
