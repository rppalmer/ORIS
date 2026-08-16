"""Build a Phoenix-shaped trace database, so the readers can be tested offline.

Only the columns ORIS reads are created. That is deliberate: the point of the
tests is that ORIS's queries work against Phoenix's layout, and a hand-copied
full schema would go stale faster than it would catch anything.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE traces (
    id INTEGER PRIMARY KEY,
    trace_id TEXT,
    project_rowid INTEGER,
    start_time TEXT,
    end_time TEXT
);
CREATE TABLE spans (
    id INTEGER PRIMARY KEY,
    trace_rowid INTEGER,
    span_id TEXT,
    parent_id TEXT,
    name TEXT,
    span_kind TEXT,
    start_time TEXT,
    end_time TEXT,
    attributes TEXT,
    status_code TEXT,
    cumulative_error_count INTEGER,
    cumulative_llm_token_count_prompt INTEGER
);
"""


def _root_attributes(request: str, mode: str, thread_id: str) -> str:
    """Reproduce what OpenInference records on a LangGraph root span."""
    return json.dumps(
        {
            "metadata": {"thread_id": thread_id, "ls_integration": "langgraph"},
            "input": {
                "mime_type": "application/json",
                "value": json.dumps(
                    {
                        "messages": [{"type": "human", "data": {"content": request}}],
                        "mode": mode,
                    }
                ),
            },
        }
    )


def write_trace(
    database_path: Path,
    *,
    trace_id: str,
    request: str,
    mode: str = "threat_intel",
    thread_id: str = "thread-1",
    started_at: datetime,
    elapsed_seconds: float = 10.0,
    prompt_tokens: int = 1200,
    error_count: int = 0,
    system_prompt: str | None = None,
    project: str = "oris",
) -> None:
    """Append one complete run: a root span, a child node, and a model call."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not database_path.exists()
    with closing(sqlite3.connect(database_path)) as con, con:
        if fresh:
            con.executescript(SCHEMA)
        project_row = con.execute(
            "SELECT id FROM projects WHERE name = ?", (project,)
        ).fetchone()
        if project_row is None:
            project_rowid = con.execute(
                "INSERT INTO projects (name) VALUES (?)", (project,)
            ).lastrowid
        else:
            project_rowid = project_row[0]

        finished = started_at + timedelta(seconds=elapsed_seconds)
        trace_rowid = con.execute(
            "INSERT INTO traces (trace_id, project_rowid, start_time, end_time)"
            " VALUES (?, ?, ?, ?)",
            (trace_id, project_rowid, _stamp(started_at), _stamp(finished)),
        ).lastrowid

        llm_attributes = json.dumps(
            {
                "llm": {
                    "input_messages": [
                        {"message": {"role": "system", "content": system_prompt}},
                        {"message": {"role": "user", "content": request}},
                    ]
                }
            }
        )
        spans = [
            (
                f"{trace_id}-root",
                None,
                "LangGraph",
                "CHAIN",
                0.0,
                elapsed_seconds,
                _root_attributes(request, mode, thread_id),
                "OK",
            ),
            (
                f"{trace_id}-node",
                f"{trace_id}-root",
                mode or "chat",
                "CHAIN",
                0.5,
                elapsed_seconds - 1,
                None,
                "ERROR" if error_count else "OK",
            ),
            (
                f"{trace_id}-llm",
                f"{trace_id}-node",
                "ChatOpenAI",
                "LLM",
                1.0,
                elapsed_seconds - 2,
                llm_attributes if system_prompt else None,
                "OK",
            ),
        ]
        for span_id, parent, name, kind, offset, duration, attributes, status in spans:
            con.execute(
                "INSERT INTO spans (trace_rowid, span_id, parent_id, name, span_kind,"
                " start_time, end_time, attributes, status_code,"
                " cumulative_error_count, cumulative_llm_token_count_prompt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_rowid,
                    span_id,
                    parent,
                    name,
                    kind,
                    _stamp(started_at + timedelta(seconds=offset)),
                    _stamp(started_at + timedelta(seconds=offset + duration)),
                    attributes,
                    status,
                    error_count,
                    prompt_tokens,
                ),
            )


def _stamp(moment: datetime) -> str:
    """Phoenix stores naive UTC strings, so the fixture must too."""
    return moment.replace(tzinfo=None).isoformat(sep=" ")
