"""Tests for manual scheduled-job execution and retained run history."""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.tools import BaseTool

from oris.knowledge import KnowledgeRepository
from oris.scheduled_runs import (
    PodcastCatchUpScheduledRunRecord,
    ScheduledRunRecord,
    run_scheduled_job,
)
from oris.schedules import (
    PodcastCatchUpScheduledJob,
    WebResearchScheduledJob,
)
from oris.search import WebSearchResult
from oris.web_research import CitedAnswer

TEST_CURRENT_DATE = date(2026, 8, 8)


class SuccessfulWebResearchGraph:
    """Return one deterministic cited result while recording graph inputs."""

    def __init__(self) -> None:
        self.inputs: list[dict[str, object]] = []

    async def ainvoke(self, graph_input: dict[str, object]) -> dict:
        self.inputs.append(graph_input)
        return {
            "answer": CitedAnswer(answer="LangGraph supports workflows [1]."),
            "sources": (
                WebSearchResult(
                    title="LangGraph overview",
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    snippet="LangGraph supports stateful agent workflows.",
                ),
            ),
        }


class FailingWebResearchGraph:
    """Raise one deterministic provider-style failure."""

    async def ainvoke(self, graph_input: dict[str, object]) -> dict:
        raise RuntimeError(f"Search unavailable for {graph_input['query']}")


def make_job(*, enabled: bool = True) -> WebResearchScheduledJob:
    """Create the first allowlisted scheduled research job."""
    return WebResearchScheduledJob(
        id="weekday-ai-news",
        enabled=enabled,
        cron="0 7 * * mon-fri",
        task="web_research",
        prompt="Research important AI-agent developments from yesterday.",
        date_window="previous_day",
        search_category="news",
    )


def make_podcast_job(*, enabled: bool = True) -> PodcastCatchUpScheduledJob:
    """Create one bounded scheduled Podcast Catch-up job."""
    return PodcastCatchUpScheduledJob(
        id="podcast-catch-up",
        enabled=enabled,
        cron="0 8 * * *",
        task="podcast_catch_up",
        days=7,
        max_episodes=2,
    )


def podcast_result(*, empty: bool = False) -> dict:
    """Return one validated prepared Podcast Catch-up result."""
    if empty:
        return {
            "answer": "No new podcast episodes were found.",
            "cited_urls": [],
            "episodes": [],
            "caveats": [],
            "transcript_call_ids": [],
        }
    return {
        "answer": "A concise scheduled digest.",
        "cited_urls": ["https://example.com/episode-1"],
        "episodes": [
            {
                "episode_id": "episode-1",
                "title": "Episode 1",
                "show": "Example Show",
                "published_at": "2026-08-08T12:00:00+00:00",
                "url": "https://example.com/episode-1",
                "summary": "The episode explains one useful idea.",
                "transcript_backend": "whisper",
                "transcript_created_now": True,
                "transcript_truncated": True,
            }
        ],
        "caveats": ["Transcript truncated for Episode 1."],
        "transcript_call_ids": ["transcript-call-1"],
    }


def make_podcast_builder(
    result: dict,
    acknowledgement_tool: Mock,
    *,
    graph_error: Exception | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Return an async component builder and its preparation graph."""
    preparation_graph = AsyncMock()
    if graph_error is None:
        preparation_graph.ainvoke.return_value = result
    else:
        preparation_graph.ainvoke.side_effect = graph_error
    builder = AsyncMock(return_value=(preparation_graph, acknowledgement_tool))
    return builder, preparation_graph


def make_acknowledgement_tool() -> Mock:
    """Create the approved Net-Razor acknowledgement tool double."""
    tool = Mock(spec=BaseTool)
    tool.name = "net_razor_podcast_mark_processed"
    tool.ainvoke = AsyncMock(return_value={"errors": []})
    return tool


def load_only_record(artifact_root: Path) -> ScheduledRunRecord:
    """Load the one JSON history record produced by a test run."""
    record_paths = list(artifact_root.rglob("*.json"))
    assert len(record_paths) == 1
    return ScheduledRunRecord.model_validate_json(
        record_paths[0].read_text(encoding="utf-8")
    )


def load_only_podcast_record(
    artifact_root: Path,
) -> PodcastCatchUpScheduledRunRecord:
    """Load the one retained Podcast Catch-up history record."""
    record_paths = list(artifact_root.rglob("*.json"))
    assert len(record_paths) == 1
    return PodcastCatchUpScheduledRunRecord.model_validate_json(
        record_paths[0].read_text(encoding="utf-8")
    )


def test_successful_run_writes_history_report_and_knowledge(tmp_path) -> None:
    """One successful invocation retains each approved scheduled output."""
    graph = SuccessfulWebResearchGraph()
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    artifact_root = tmp_path / "scheduled"

    record = run_scheduled_job(
        make_job(),
        graph,
        repository,
        current_date=TEST_CURRENT_DATE,
        artifact_root=artifact_root,
    )

    assert graph.inputs == [
        {
            "query": "Research important AI-agent developments from yesterday.",
            "search_category": "news",
            "start_date": date(2026, 8, 7),
            "end_date": date(2026, 8, 8),
        }
    ]
    assert record.status == "succeeded"
    assert record.search_category == "news"
    assert record.start_date == date(2026, 8, 7)
    assert record.end_date == date(2026, 8, 8)
    assert load_only_record(artifact_root) == record

    report_paths = list(artifact_root.rglob("*.md"))
    assert len(report_paths) == 1
    report = report_paths[0].read_text(encoding="utf-8")
    assert "LangGraph supports workflows [1]." in report
    assert "Date range: `2026-08-07` through `2026-08-08`" in report
    assert (
        "[LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)"
        in report
    )

    matches = repository.search("LangGraph", source_type="scheduled_run")
    assert [match.document_id for match in matches] == [str(record.run_id)]
    assert matches[0].source_ref == record.report_path


def test_failed_run_writes_history_without_report_or_knowledge(tmp_path) -> None:
    """A graph failure remains visible without publishing a successful result."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    artifact_root = tmp_path / "scheduled"

    with pytest.raises(RuntimeError, match="Search unavailable"):
        run_scheduled_job(
            make_job(),
            FailingWebResearchGraph(),
            repository,
            current_date=TEST_CURRENT_DATE,
            artifact_root=artifact_root,
        )

    record = load_only_record(artifact_root)
    assert record.status == "failed"
    assert record.finished_at is not None
    assert record.report_path is None
    assert record.error is not None
    assert record.error.startswith("RuntimeError: Search unavailable")
    assert list(artifact_root.rglob("*.md")) == []
    assert repository.search("developments", source_type="scheduled_run") == ()


def test_disabled_job_is_not_attempted(tmp_path) -> None:
    """Manual execution cannot bypass the schedule's enabled flag."""
    artifact_root = tmp_path / "scheduled"

    with pytest.raises(ValueError, match="disabled"):
        run_scheduled_job(
            make_job(enabled=False),
            SuccessfulWebResearchGraph(),
            KnowledgeRepository(tmp_path / "knowledge.sqlite"),
            current_date=TEST_CURRENT_DATE,
            artifact_root=artifact_root,
        )

    assert not artifact_root.exists()


def test_scheduled_podcast_persists_before_acknowledgement(tmp_path) -> None:
    """A complete report and knowledge entry exist before acknowledgement.

    Acknowledgement is one-way: an episode leaves Net-Razor's queue and does not
    come back. Doing it before the deliverable is safely written would lose the
    episode and the report together.
    """
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    artifact_root = tmp_path / "scheduled"
    acknowledgement = make_acknowledgement_tool()

    async def acknowledge_after_persistence(tool_call: dict) -> dict:
        report_paths = list(artifact_root.rglob("*.md"))
        assert len(report_paths) == 1
        assert repository.search("useful idea", source_type="scheduled_run")
        return {"errors": []}

    acknowledgement.ainvoke.side_effect = acknowledge_after_persistence
    builder, preparation_graph = make_podcast_builder(podcast_result(), acknowledgement)

    record = run_scheduled_job(
        make_podcast_job(),
        Mock(),
        repository,
        current_date=TEST_CURRENT_DATE,
        artifact_root=artifact_root,
        build_podcast_catch_up=builder,
    )

    preparation_graph.ainvoke.assert_awaited_once_with({"days": 7, "max_episodes": 2})
    acknowledgement.ainvoke.assert_awaited_once()
    assert acknowledgement.ainvoke.await_args.args[0]["args"] == {
        "call_ids": ["transcript-call-1"]
    }
    assert record.status == "succeeded"
    assert record.report_path is not None
    assert load_only_podcast_record(artifact_root) == record

    report = next(artifact_root.rglob("*.md")).read_text(encoding="utf-8")
    assert "A concise scheduled digest." in report
    assert "[Episode 1](https://example.com/episode-1)" in report
    assert "The episode explains one useful idea." in report
    # Not the backend name: the reader is being told how much to trust the
    # words, and "whisper" only says that to someone who already knows.
    assert "transcribed by ORIS during this run" in report
    assert "Transcript truncated for Episode 1." in report
    # Receipts are internal plumbing and never belong in a deliverable.
    assert "transcript-call-1" not in report


def test_scheduled_podcast_writes_an_empty_success_report(tmp_path) -> None:
    """An empty queue remains visible without an acknowledgement call."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    artifact_root = tmp_path / "scheduled"
    acknowledgement = make_acknowledgement_tool()
    builder, _ = make_podcast_builder(podcast_result(empty=True), acknowledgement)

    record = run_scheduled_job(
        make_podcast_job(),
        Mock(),
        repository,
        current_date=TEST_CURRENT_DATE,
        artifact_root=artifact_root,
        build_podcast_catch_up=builder,
    )

    assert record.status == "succeeded"
    acknowledgement.ainvoke.assert_not_awaited()
    report = next(artifact_root.rglob("*.md")).read_text(encoding="utf-8")
    assert "No new podcast episodes were found." in report


def test_scheduled_podcast_failure_before_report_does_not_acknowledge(tmp_path) -> None:
    """Preparation failure leaves no deliverable and no processed episodes."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    artifact_root = tmp_path / "scheduled"
    acknowledgement = make_acknowledgement_tool()
    builder, _ = make_podcast_builder(
        podcast_result(),
        acknowledgement,
        graph_error=RuntimeError("digest failed"),
    )

    with pytest.raises(RuntimeError, match="digest failed"):
        run_scheduled_job(
            make_podcast_job(),
            Mock(),
            repository,
            current_date=TEST_CURRENT_DATE,
            artifact_root=artifact_root,
            build_podcast_catch_up=builder,
        )

    record = load_only_podcast_record(artifact_root)
    assert record.status == "failed"
    assert record.report_path is None
    assert record.error is not None
    assert record.error.startswith("preparing Podcast Catch-up: RuntimeError")
    assert list(artifact_root.rglob("*.md")) == []
    acknowledgement.ainvoke.assert_not_awaited()


def test_scheduled_podcast_acknowledgement_failure_retains_report(tmp_path) -> None:
    """An uncertain acknowledgement cannot erase the completed deliverable.

    The safe direction is for an episode to appear again, never to vanish.
    """
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    artifact_root = tmp_path / "scheduled"
    acknowledgement = make_acknowledgement_tool()
    acknowledgement.ainvoke.side_effect = RuntimeError("acknowledgement unavailable")
    builder, _ = make_podcast_builder(podcast_result(), acknowledgement)

    with pytest.raises(RuntimeError, match="acknowledgement unavailable"):
        run_scheduled_job(
            make_podcast_job(),
            Mock(),
            repository,
            current_date=TEST_CURRENT_DATE,
            artifact_root=artifact_root,
            build_podcast_catch_up=builder,
        )

    record = load_only_podcast_record(artifact_root)
    assert record.status == "failed"
    assert record.report_path is not None
    assert record.error is not None
    assert record.error.startswith("acknowledging podcast episodes: RuntimeError")
    assert len(list(artifact_root.rglob("*.md"))) == 1
    assert repository.search("useful idea", source_type="scheduled_run")
