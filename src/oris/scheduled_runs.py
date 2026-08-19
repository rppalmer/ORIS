"""Manual execution and durable history for configured scheduled jobs."""

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import AwareDatetime, BaseModel, ConfigDict

from oris.knowledge import KnowledgeDocument, KnowledgeRepository
from oris.podcast_catch_up import (
    PreparedPodcastCatchUpOutput,
    acknowledge_podcast_catch_up,
)
from oris.schedules import (
    ConfiguredScheduledJob,
    JobId,
    PodcastCatchUpScheduledJob,
    WebResearchScheduledJob,
    YouTubeCatchUpScheduledJob,
    load_schedule_config,
)
from oris.search import SearchCategory
from oris.youtube_catch_up import (
    PreparedYouTubeCatchUpOutput,
    acknowledge_youtube_catch_up,
)


class ScheduledRunRecordBase(BaseModel):
    """History fields shared by every scheduled-job attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: JobId
    run_id: UUID
    started_at: AwareDatetime
    status: Literal["running", "succeeded", "failed"]
    finished_at: AwareDatetime | None = None
    report_path: str | None = None
    error: str | None = None


class ScheduledRunRecord(ScheduledRunRecordBase):
    """Durable history for one scheduled Web Research attempt."""

    task: Literal["web_research"]
    prompt: str
    search_category: SearchCategory
    start_date: date
    end_date: date


class YouTubeCatchUpScheduledRunRecord(ScheduledRunRecordBase):
    """Durable history for one scheduled YouTube Catch-up attempt."""

    task: Literal["youtube_catch_up"]
    days: int
    max_videos: int


class PodcastCatchUpScheduledRunRecord(ScheduledRunRecordBase):
    """Durable history for one scheduled Podcast Catch-up attempt."""

    task: Literal["podcast_catch_up"]
    days: int
    max_episodes: int


YouTubeCatchUpBuilder = Callable[[], Awaitable[tuple[CompiledStateGraph, BaseTool]]]
PodcastCatchUpBuilder = Callable[[], Awaitable[tuple[CompiledStateGraph, BaseTool]]]


def _write_text_atomically(path: Path, content: str) -> None:
    """Replace a local run file without exposing partially written content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_run_record(
    path: Path,
    record: ScheduledRunRecordBase,
) -> None:
    """Persist one run record as readable JSON."""
    _write_text_atomically(path, f"{record.model_dump_json(indent=2)}\n")


def _format_web_research_report(
    job: WebResearchScheduledJob,
    run_id: UUID,
    start_date: date,
    end_date: date,
    result: dict,
) -> str:
    """Format a successful Web Research result as Markdown."""
    source_lines = "\n".join(
        f"{number}. [{source.title}]({source.url})"
        for number, source in enumerate(result["sources"], start=1)
    )
    return (
        f"# Scheduled research: {job.id}\n\n"
        f"- Run ID: `{run_id}`\n"
        f"- Prompt: {job.prompt}\n"
        f"- Search category: `{job.search_category}`\n"
        f"- Date range: `{start_date}` through `{end_date}` (end exclusive)\n\n"
        f"## Answer\n\n{result['answer'].answer}\n\n"
        f"## Sources\n\n{source_lines}\n"
    )


def _format_youtube_catch_up_report(
    job: YouTubeCatchUpScheduledJob,
    run_id: UUID,
    result: PreparedYouTubeCatchUpOutput,
) -> str:
    """Format one validated YouTube Catch-up result as Markdown."""
    video_sections = []
    for video in result["videos"]:
        transcript_status = "truncated" if video["transcript_truncated"] else "complete"
        video_sections.append(
            f"### [{video['title']}]({video['url']})\n\n"
            f"- Channel: {video['channel']}\n"
            f"- Published: `{video['published_at']}`\n"
            f"- Transcript: `{transcript_status}`\n\n"
            f"{video['summary']}"
        )
    videos = "\n\n".join(video_sections) or "No videos were summarized."

    titles_by_url = {video["url"]: video["title"] for video in result["videos"]}
    sources = (
        "\n".join(
            f"{number}. [{titles_by_url.get(url, url)}]({url})"
            for number, url in enumerate(result["cited_urls"], start=1)
        )
        or "No sources were cited."
    )
    caveats = "\n".join(f"- {caveat}" for caveat in result["caveats"]) or "None."

    return (
        f"# Scheduled YouTube catch-up: {job.id}\n\n"
        f"- Run ID: `{run_id}`\n"
        f"- Lookback: `{job.days}` days\n"
        f"- Maximum videos: `{job.max_videos}`\n\n"
        f"## Digest\n\n{result['answer']}\n\n"
        f"## Videos\n\n{videos}\n\n"
        f"## Sources\n\n{sources}\n\n"
        f"## Caveats\n\n{caveats}\n"
    )


def _run_scheduled_web_research_job(
    job: WebResearchScheduledJob,
    web_research_graph: CompiledStateGraph,
    knowledge_repository: KnowledgeRepository,
    *,
    current_date: date,
    artifact_root: Path = Path("artifacts/scheduled"),
) -> ScheduledRunRecord:
    """Run one enabled Web Research job and retain its outputs."""
    if not job.enabled:
        raise ValueError(f"Scheduled job is disabled: {job.id}")

    start_date = current_date - timedelta(days=1)
    end_date = current_date
    run_id = uuid4()
    started_at = datetime.now(UTC)
    run_stem = f"{started_at:%Y%m%dT%H%M%SZ}-{run_id}"
    job_directory = artifact_root / job.id
    record_path = job_directory / f"{run_stem}.json"
    report_path = job_directory / f"{run_stem}.md"
    relative_report_path = Path(job.id) / report_path.name

    record = ScheduledRunRecord(
        job_id=job.id,
        run_id=run_id,
        task=job.task,
        prompt=job.prompt,
        search_category=job.search_category,
        start_date=start_date,
        end_date=end_date,
        started_at=started_at,
        status="running",
    )
    _write_run_record(record_path, record)

    try:
        # The search this reaches is asynchronous so that it has a deadline at
        # all; an unbounded one here holds the job's only slot and every later
        # firing is skipped without a word.
        result = asyncio.run(
            web_research_graph.ainvoke(
                {
                    "query": job.prompt,
                    "search_category": job.search_category,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            )
        )
        report = _format_web_research_report(
            job,
            run_id,
            start_date,
            end_date,
            result,
        )
        _write_text_atomically(report_path, report)

        finished_at = datetime.now(UTC)
        knowledge_repository.add(
            KnowledgeDocument(
                document_id=str(run_id),
                source_type="scheduled_run",
                source_ref=str(relative_report_path),
                created_at=finished_at,
                title=f"Scheduled research: {job.id}",
                content=report,
            )
        )
    except Exception as error:
        report_path.unlink(missing_ok=True)
        failed_record = record.model_copy(
            update={
                "finished_at": datetime.now(UTC),
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
        )
        _write_run_record(record_path, failed_record)
        raise

    succeeded_record = record.model_copy(
        update={
            "finished_at": finished_at,
            "status": "succeeded",
            "report_path": str(relative_report_path),
        }
    )
    _write_run_record(record_path, succeeded_record)
    return succeeded_record


async def run_scheduled_youtube_catch_up_job(
    job: YouTubeCatchUpScheduledJob,
    build_youtube_catch_up: YouTubeCatchUpBuilder,
    knowledge_repository: KnowledgeRepository,
    *,
    artifact_root: Path = Path("artifacts/scheduled"),
) -> YouTubeCatchUpScheduledRunRecord:
    """Run one YouTube job, persist its report, then acknowledge its videos."""
    if not job.enabled:
        raise ValueError(f"Scheduled job is disabled: {job.id}")

    run_id = uuid4()
    started_at = datetime.now(UTC)
    run_stem = f"{started_at:%Y%m%dT%H%M%SZ}-{run_id}"
    job_directory = artifact_root / job.id
    record_path = job_directory / f"{run_stem}.json"
    report_path = job_directory / f"{run_stem}.md"
    relative_report_path = Path(job.id) / report_path.name

    record = YouTubeCatchUpScheduledRunRecord(
        job_id=job.id,
        run_id=run_id,
        task=job.task,
        days=job.days,
        max_videos=job.max_videos,
        started_at=started_at,
        status="running",
    )
    _write_run_record(record_path, record)

    retained_record = record
    phase = "building YouTube Catch-up"
    try:
        preparation_graph, acknowledgement_tool = await build_youtube_catch_up()
        phase = "preparing YouTube Catch-up"
        result = await preparation_graph.ainvoke(
            {"days": job.days, "max_videos": job.max_videos}
        )

        phase = "formatting YouTube report"
        report = _format_youtube_catch_up_report(job, run_id, result)
        phase = "writing YouTube report"
        _write_text_atomically(report_path, report)

        retained_record = record.model_copy(
            update={"report_path": str(relative_report_path)}
        )
        phase = "recording YouTube report path"
        _write_run_record(record_path, retained_record)

        phase = "indexing YouTube report"
        knowledge_repository.add(
            KnowledgeDocument(
                document_id=str(run_id),
                source_type="scheduled_run",
                source_ref=str(relative_report_path),
                created_at=datetime.now(UTC),
                title=f"Scheduled YouTube catch-up: {job.id}",
                content=report,
            )
        )

        phase = "acknowledging YouTube videos"
        await acknowledge_youtube_catch_up(
            acknowledgement_tool,
            result["transcript_call_ids"],
        )
    except Exception as error:
        if retained_record.report_path is None:
            report_path.unlink(missing_ok=True)
        failed_record = retained_record.model_copy(
            update={
                "finished_at": datetime.now(UTC),
                "status": "failed",
                "error": f"{phase}: {type(error).__name__}: {error}",
            }
        )
        _write_run_record(record_path, failed_record)
        raise

    succeeded_record = retained_record.model_copy(
        update={
            "finished_at": datetime.now(UTC),
            "status": "succeeded",
        }
    )
    _write_run_record(record_path, succeeded_record)
    return succeeded_record


def run_scheduled_job(
    job: ConfiguredScheduledJob,
    web_research_graph: CompiledStateGraph,
    knowledge_repository: KnowledgeRepository,
    *,
    current_date: date,
    artifact_root: Path = Path("artifacts/scheduled"),
    build_youtube_catch_up: YouTubeCatchUpBuilder | None = None,
    build_podcast_catch_up: PodcastCatchUpBuilder | None = None,
) -> ScheduledRunRecordBase:
    """Run one configured job through its fixed specialist path."""
    if isinstance(job, WebResearchScheduledJob):
        return _run_scheduled_web_research_job(
            job,
            web_research_graph,
            knowledge_repository,
            current_date=current_date,
            artifact_root=artifact_root,
        )
    if isinstance(job, PodcastCatchUpScheduledJob):
        if build_podcast_catch_up is None:
            raise ValueError("Podcast Catch-up dependencies are not configured")
        return asyncio.run(
            run_scheduled_podcast_catch_up_job(
                job,
                build_podcast_catch_up,
                knowledge_repository,
                artifact_root=artifact_root,
            )
        )
    if build_youtube_catch_up is None:
        raise ValueError("YouTube Catch-up dependencies are not configured")
    return asyncio.run(
        run_scheduled_youtube_catch_up_job(
            job,
            build_youtube_catch_up,
            knowledge_repository,
            artifact_root=artifact_root,
        )
    )


def main() -> None:
    """Run one named job manually without starting a scheduler."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("job_id", help="Enabled job ID from schedules.toml")
    parser.add_argument(
        "--schedule-file",
        type=Path,
        default=Path("schedules.toml"),
        help="Schedule configuration path (default: schedules.toml)",
    )
    args = parser.parse_args()

    schedule_config = load_schedule_config(args.schedule_file)
    job = next((item for item in schedule_config.jobs if item.id == args.job_id), None)
    if job is None:
        parser.error(f"Unknown scheduled job: {args.job_id}")
    if not job.enabled:
        parser.error(f"Scheduled job is disabled: {args.job_id}")

    from oris.web_research_app import (
        build_podcast_catch_up_preparation,
        build_youtube_catch_up_preparation,
        knowledge_repository,
        web_research_graph,
    )

    current_date = datetime.now(ZoneInfo(schedule_config.timezone)).date()
    record = run_scheduled_job(
        job,
        web_research_graph,
        knowledge_repository,
        current_date=current_date,
        build_youtube_catch_up=build_youtube_catch_up_preparation,
        build_podcast_catch_up=build_podcast_catch_up_preparation,
    )
    print(f"Scheduled run succeeded: {record.report_path}")


def _format_podcast_catch_up_report(
    job: PodcastCatchUpScheduledJob,
    run_id: UUID,
    result: PreparedPodcastCatchUpOutput,
) -> str:
    """Format one validated Podcast Catch-up result as Markdown.

    Every episode states where its transcript came from. A machine transcript
    gets names, acronyms, and version numbers wrong, and a reader who cannot
    tell which episodes were machine-transcribed will weigh a mangled product
    name exactly as heavily as one the publisher wrote down.
    """
    episode_sections = []
    for episode in result["episodes"]:
        transcript_status = (
            "truncated" if episode["transcript_truncated"] else "complete"
        )
        episode_sections.append(
            f"### [{episode['title']}]({episode['url']})\n\n"
            f"- Show: {episode['show']}\n"
            f"- Published: `{episode['published_at']}`\n"
            f"- Transcript: `{episode['transcript_backend']}`, `{transcript_status}`\n\n"
            f"{episode['summary']}"
        )
    episodes = "\n\n".join(episode_sections) or "No episodes were summarized."

    titles_by_url = {episode["url"]: episode["title"] for episode in result["episodes"]}
    sources = (
        "\n".join(
            f"{number}. [{titles_by_url.get(url, url)}]({url})"
            for number, url in enumerate(result["cited_urls"], start=1)
        )
        or "No sources were cited."
    )
    caveats = "\n".join(f"- {caveat}" for caveat in result["caveats"]) or "None."

    return (
        f"# Scheduled podcast catch-up: {job.id}\n\n"
        f"- Run ID: `{run_id}`\n"
        f"- Lookback: `{job.days}` days\n"
        f"- Maximum episodes: `{job.max_episodes}`\n\n"
        f"## Digest\n\n{result['answer']}\n\n"
        f"## Episodes\n\n{episodes}\n\n"
        f"## Sources\n\n{sources}\n\n"
        f"## Caveats\n\n{caveats}\n"
    )


async def run_scheduled_podcast_catch_up_job(
    job: PodcastCatchUpScheduledJob,
    build_podcast_catch_up: PodcastCatchUpBuilder,
    knowledge_repository: KnowledgeRepository,
    *,
    artifact_root: Path = Path("artifacts/scheduled"),
) -> PodcastCatchUpScheduledRunRecord:
    """Run one podcast job, persist its report, then acknowledge its episodes."""
    if not job.enabled:
        raise ValueError(f"Scheduled job is disabled: {job.id}")

    run_id = uuid4()
    started_at = datetime.now(UTC)
    run_stem = f"{started_at:%Y%m%dT%H%M%SZ}-{run_id}"
    job_directory = artifact_root / job.id
    record_path = job_directory / f"{run_stem}.json"
    report_path = job_directory / f"{run_stem}.md"
    relative_report_path = Path(job.id) / report_path.name

    record = PodcastCatchUpScheduledRunRecord(
        job_id=job.id,
        run_id=run_id,
        task=job.task,
        days=job.days,
        max_episodes=job.max_episodes,
        started_at=started_at,
        status="running",
    )
    _write_run_record(record_path, record)

    retained_record = record
    phase = "building Podcast Catch-up"
    try:
        preparation_graph, acknowledgement_tool = await build_podcast_catch_up()
        phase = "preparing Podcast Catch-up"
        result = await preparation_graph.ainvoke(
            {"days": job.days, "max_episodes": job.max_episodes}
        )

        phase = "formatting podcast report"
        report = _format_podcast_catch_up_report(job, run_id, result)
        phase = "writing podcast report"
        _write_text_atomically(report_path, report)

        retained_record = record.model_copy(
            update={"report_path": str(relative_report_path)}
        )
        phase = "recording podcast report path"
        _write_run_record(record_path, retained_record)

        phase = "indexing podcast report"
        knowledge_repository.add(
            KnowledgeDocument(
                document_id=str(run_id),
                source_type="scheduled_run",
                source_ref=str(relative_report_path),
                created_at=datetime.now(UTC),
                title=f"Scheduled podcast catch-up: {job.id}",
                content=report,
            )
        )

        phase = "acknowledging podcast episodes"
        await acknowledge_podcast_catch_up(
            acknowledgement_tool,
            result["transcript_call_ids"],
        )
    except Exception as error:
        if retained_record.report_path is None:
            report_path.unlink(missing_ok=True)
        failed_record = retained_record.model_copy(
            update={
                "finished_at": datetime.now(UTC),
                "status": "failed",
                "error": f"{phase}: {type(error).__name__}: {error}",
            }
        )
        _write_run_record(record_path, failed_record)
        raise

    succeeded_record = retained_record.model_copy(
        update={"finished_at": datetime.now(UTC), "status": "succeeded"}
    )
    _write_run_record(record_path, succeeded_record)
    return succeeded_record
