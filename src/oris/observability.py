"""Read-only access to the local Phoenix trace store.

Phoenix keeps its traces in an ordinary SQLite file, so reading them back needs
no client library, no pandas, and no running Phoenix server. This module answers
one question the Phoenix web UI cannot: what did the turn I am looking at cost.
Anything deeper — waterfalls, search, span payloads — belongs in Phoenix itself,
which is already running and better at it.

Every query is read-only and every failure returns empty. Tracing is optional,
the file may not exist, and its schema belongs to Phoenix rather than to ORIS:
none of that is a reason to fail a chat session.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_NAME = "oris"
# One `graph.ainvoke` produces one trace whose root span LangGraph names itself.
ROOT_SPAN_NAME = "LangGraph"


@dataclass(frozen=True)
class Trace:
    """One graph run: what was asked, where it went, and what it cost."""

    trace_id: str
    started_at: datetime
    elapsed_seconds: float
    prompt_tokens: int
    error_count: int
    request: str
    mode: str
    thread_id: str

    @property
    def failed(self) -> bool:
        return self.error_count > 0


@dataclass(frozen=True)
class Span:
    """One step inside a run: a node, a tool call, or a model call."""

    name: str
    kind: str
    elapsed_seconds: float
    status: str
    prompt_tokens: int
    depth: int


@dataclass(frozen=True)
class SystemPrompt:
    """The system prompt one model call was given, as it was sent."""

    span_name: str
    content: str


@contextmanager
def _connect(database_path: Path) -> Iterator[sqlite3.Connection | None]:
    """Open the trace store read-only, yielding None when it is unusable."""
    if not database_path.is_file():
        yield None
        return
    try:
        with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            yield con
    except sqlite3.Error:
        yield None


def _attributes(value: object) -> dict[str, Any]:
    """Decode one span's attributes, which Phoenix may store as text or JSON."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _nested(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if not isinstance(mapping, dict):
            return None
        mapping = mapping.get(key)
    return mapping


def _root_details(attributes: object) -> tuple[str, str, str]:
    """Recover the request, mode, and session from the root span.

    OpenInference records the arguments the graph was invoked with, so the
    question a run answered is already in the trace. Reading it back is the
    difference between a list of timings and a list of turns.
    """
    decoded = _attributes(attributes)
    thread_id = _nested(decoded, "metadata", "thread_id")
    payload = _nested(decoded, "input", "value")
    state: dict[str, Any] = {}
    if isinstance(payload, str):
        try:
            loaded = json.loads(payload)
        except ValueError:
            loaded = None
        state = loaded if isinstance(loaded, dict) else {}
    messages = state.get("messages")
    request = ""
    if isinstance(messages, list) and messages:
        request = _nested(messages[0], "data", "content") or ""
    return (
        str(request),
        str(state.get("mode") or ""),
        str(thread_id or ""),
    )


def _utc(value: str) -> datetime:
    """Phoenix stores naive UTC; say so, so callers can compare and localise."""
    stamp = datetime.fromisoformat(value)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    try:
        return max(
            (
                datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds(),
            0.0,
        )
    except ValueError:
        return 0.0


def newest_trace_at(database_path: Path) -> datetime | None:
    """Return when the store last recorded anything, across every session.

    An empty activity view has two very different causes: nothing has been
    traced at all, or nothing has been traced *for the session being viewed*.
    Only the store can tell them apart, and the age of its newest entry is what
    reveals a collector that stopped days ago while the setting stayed on.
    """
    with _connect(database_path) as con:
        if con is None:
            return None
        try:
            row = con.execute(
                """
                SELECT MAX(t.start_time) FROM traces t
                  JOIN projects p ON p.id = t.project_rowid
                 WHERE p.name = ?
                """,
                (PROJECT_NAME,),
            ).fetchone()
        except sqlite3.Error:
            return None
    return _utc(row[0]) if row and row[0] else None


def recent_traces(
    database_path: Path,
    limit: int = 50,
    *,
    thread_id: str | None = None,
) -> list[Trace]:
    """Return the most recent ORIS runs, newest first.

    Passing a thread narrows the list to one conversation, which is what makes
    the activity view a companion to the chat rather than a separate report.
    """
    query = """
        SELECT t.trace_id, t.start_time, t.end_time, root.attributes AS attributes,
               (SELECT MAX(s.cumulative_llm_token_count_prompt) FROM spans s
                 WHERE s.trace_rowid = t.id) AS prompt_tokens,
               (SELECT MAX(s.cumulative_error_count) FROM spans s
                 WHERE s.trace_rowid = t.id) AS error_count
          FROM traces t
          JOIN projects p ON p.id = t.project_rowid
          LEFT JOIN spans root
                 ON root.trace_rowid = t.id AND root.parent_id IS NULL
         WHERE p.name = ?
         ORDER BY t.start_time DESC
         LIMIT ?
    """
    with _connect(database_path) as con:
        if con is None:
            return []
        try:
            # Over-fetch when filtering: the thread lives inside the span
            # attributes, so SQL cannot do the narrowing itself.
            rows = con.execute(
                query,
                (PROJECT_NAME, limit * 10 if thread_id else limit),
            ).fetchall()
        except sqlite3.Error:
            # The schema belongs to Phoenix; an upgrade must not break the chat.
            return []

    traces = []
    for row in rows:
        request, mode, row_thread_id = _root_details(row["attributes"])
        if thread_id is not None and row_thread_id != thread_id:
            continue
        traces.append(
            Trace(
                trace_id=row["trace_id"],
                started_at=_utc(row["start_time"]),
                elapsed_seconds=_seconds(row["start_time"], row["end_time"]),
                prompt_tokens=row["prompt_tokens"] or 0,
                error_count=row["error_count"] or 0,
                request=request,
                mode=mode,
                thread_id=row_thread_id,
            )
        )
        if len(traces) == limit:
            break
    return traces


def spans_for_trace(database_path: Path, trace_id: str) -> list[Span]:
    """Return one run's steps in execution order, with nesting depth."""
    query = """
        SELECT s.span_id, s.parent_id, s.name, s.span_kind, s.start_time, s.end_time,
               s.status_code, s.cumulative_llm_token_count_prompt AS prompt_tokens
          FROM spans s
          JOIN traces t ON t.id = s.trace_rowid
         WHERE t.trace_id = ?
         ORDER BY s.start_time ASC
    """
    with _connect(database_path) as con:
        if con is None:
            return []
        try:
            rows = con.execute(query, (trace_id,)).fetchall()
        except sqlite3.Error:
            return []

    parents = {row["span_id"]: row["parent_id"] for row in rows}

    def depth_of(span_id: str) -> int:
        depth = 0
        seen = {span_id}
        parent = parents.get(span_id)
        # Guard against a cycle rather than trusting foreign data to be a tree.
        while parent and parent in parents and parent not in seen:
            seen.add(parent)
            depth += 1
            parent = parents.get(parent)
        return depth

    return [
        Span(
            name=row["name"],
            kind=row["span_kind"] or "",
            elapsed_seconds=_seconds(row["start_time"], row["end_time"]),
            status=row["status_code"] or "",
            prompt_tokens=row["prompt_tokens"] or 0,
            depth=depth_of(row["span_id"]),
        )
        for row in rows
    ]


def system_prompts_for_trace(database_path: Path, trace_id: str) -> list[SystemPrompt]:
    """Return the system prompt each model call in one run was actually given.

    Reading the packaged prompt file would answer a different question. What a
    run behaved on is what reached the model: the composed prompt, after every
    template substitution, for that specific call.
    """
    query = """
        SELECT s.name, s.attributes
          FROM spans s
          JOIN traces t ON t.id = s.trace_rowid
         WHERE t.trace_id = ? AND s.span_kind = 'LLM'
         ORDER BY s.start_time ASC
    """
    with _connect(database_path) as con:
        if con is None:
            return []
        try:
            rows = con.execute(query, (trace_id,)).fetchall()
        except sqlite3.Error:
            return []

    prompts: list[SystemPrompt] = []
    seen: set[str] = set()
    for row in rows:
        messages = _nested(_attributes(row["attributes"]), "llm", "input_messages")
        if not isinstance(messages, list):
            continue
        for entry in messages:
            message = _nested(entry, "message") or {}
            if str(message.get("role") or "").casefold() != "system":
                continue
            content = str(message.get("content") or "").strip()
            # One turn can call the same specialist repeatedly; the prompt is
            # the same each time, and listing it once is what makes it readable.
            if content and content not in seen:
                seen.add(content)
                prompts.append(SystemPrompt(span_name=row["name"], content=content))
    return prompts
