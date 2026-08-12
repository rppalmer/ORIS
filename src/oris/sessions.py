"""Small local pointer for ORIS's active conversation session."""

from pathlib import Path
from uuid import UUID, uuid4

ACTIVE_SESSION_FILENAME = "current_session"


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


def load_or_create_session(path: Path) -> str:
    """Resume the persisted session or create one on first use."""
    if not path.exists():
        return start_new_session(path)

    stored_value = path.read_text(encoding="utf-8").strip()
    try:
        return str(UUID(stored_value))
    except ValueError as error:
        raise ValueError(f"Invalid active session file: {path}") from error
