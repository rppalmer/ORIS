"""Reading traces back out of Phoenix's own SQLite file."""

from datetime import UTC, datetime
from pathlib import Path

from phoenix_fixture import write_trace

from oris.observability import (
    recent_traces,
    spans_for_trace,
    system_prompts_for_trace,
)

FIRST = datetime(2026, 8, 12, 2, 4, 29, tzinfo=UTC)
SECOND = datetime(2026, 8, 12, 3, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> Path:
    database_path = tmp_path / "phoenix.db"
    write_trace(
        database_path,
        trace_id="aaa",
        request="enrich 8.8.8.8",
        started_at=FIRST,
        elapsed_seconds=19.5,
        prompt_tokens=2957,
        system_prompt="You are ORIS's Threat Intel specialist.",
    )
    write_trace(
        database_path,
        trace_id="bbb",
        request="what changed yesterday",
        mode="web_research",
        thread_id="thread-2",
        started_at=SECOND,
        elapsed_seconds=4.0,
        prompt_tokens=100,
        error_count=1,
    )
    return database_path


def test_a_missing_trace_store_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    """Tracing is optional, so every reader has to survive Phoenix never running."""
    missing = tmp_path / "phoenix.db"

    assert recent_traces(missing) == []
    assert spans_for_trace(missing, "aaa") == []
    assert system_prompts_for_trace(missing, "aaa") == []


def test_an_unrecognisable_schema_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    """The schema belongs to Phoenix; an upgrade must not break the chat."""
    database_path = tmp_path / "phoenix.db"
    database_path.write_bytes(b"not a database")

    assert recent_traces(database_path) == []


def test_runs_come_back_newest_first_with_what_they_were_asked(tmp_path: Path) -> None:
    """A list of timings is not usable; a list of turns is."""
    traces = recent_traces(_store(tmp_path))

    assert [trace.trace_id for trace in traces] == ["bbb", "aaa"]
    first = traces[1]
    assert first.request == "enrich 8.8.8.8"
    assert first.mode == "threat_intel"
    assert first.thread_id == "thread-1"
    assert first.prompt_tokens == 2957
    assert round(first.elapsed_seconds, 1) == 19.5
    assert not first.failed
    assert traces[0].failed


def test_started_at_is_timezone_aware_although_phoenix_stores_naive(
    tmp_path: Path,
) -> None:
    """Naive timestamps cannot be compared to report times or shown locally."""
    started_at = recent_traces(_store(tmp_path))[1].started_at

    assert started_at.tzinfo is not None
    assert started_at == FIRST


def test_one_session_can_be_singled_out(tmp_path: Path) -> None:
    """The activity view is a companion to a conversation, not a global report."""
    traces = recent_traces(_store(tmp_path), thread_id="thread-2")

    assert [trace.trace_id for trace in traces] == ["bbb"]
    assert recent_traces(_store(tmp_path), thread_id="nobody") == []


def test_spans_carry_their_nesting_depth(tmp_path: Path) -> None:
    """Without depth the steps read as a flat list and the structure is lost."""
    spans = spans_for_trace(_store(tmp_path), "aaa")

    assert [(span.name, span.depth) for span in spans] == [
        ("LangGraph", 0),
        ("threat_intel", 1),
        ("ChatOpenAI", 2),
    ]


def test_the_prompt_returned_is_the_one_the_model_was_given(tmp_path: Path) -> None:
    """What a run behaved on is what reached the model, not the file on disk."""
    prompts = system_prompts_for_trace(_store(tmp_path), "aaa")

    assert [prompt.span_name for prompt in prompts] == ["ChatOpenAI"]
    assert prompts[0].content == "You are ORIS's Threat Intel specialist."


def test_a_run_without_model_calls_reports_no_prompts(tmp_path: Path) -> None:
    """An empty list is the honest answer; an invented prompt would not be."""
    assert system_prompts_for_trace(_store(tmp_path), "bbb") == []
