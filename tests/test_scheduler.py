"""Tests for the local APScheduler runtime."""

import asyncio
from datetime import datetime
from time import monotonic
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from oris.scheduler import create_scheduler, run_until_stopped
from oris.schedules import (
    PodcastCatchUpScheduledJob,
    ScheduleConfig,
    WebResearchScheduledJob,
)


def make_job(job_id: str, *, enabled: bool, cron: str) -> WebResearchScheduledJob:
    """Create one valid previous-day news job."""
    return WebResearchScheduledJob(
        id=job_id,
        enabled=enabled,
        cron=cron,
        task="web_research",
        prompt="Research important AI-agent developments from yesterday.",
        date_window="previous_day",
        search_category="news",
    )


def test_create_scheduler_registers_only_enabled_jobs() -> None:
    """Enabled jobs receive their configured cron trigger without executing."""
    timezone = ZoneInfo("America/Detroit")
    enabled_job = make_job(
        "weekday-ai-news",
        enabled=True,
        cron="0 7 * * mon-fri",
    )
    config = ScheduleConfig(
        timezone="America/Detroit",
        jobs=(
            enabled_job,
            make_job("disabled-news", enabled=False, cron="0 8 * * *"),
        ),
    )
    run_job = Mock()

    scheduler = create_scheduler(config, run_job)

    jobs = scheduler.get_jobs()
    assert [job.id for job in jobs] == ["weekday-ai-news"]
    assert jobs[0].args == (enabled_job,)
    assert jobs[0].coalesce is True
    assert jobs[0].max_instances == 1
    assert jobs[0].trigger.get_next_fire_time(
        None,
        datetime(2026, 8, 8, 12, tzinfo=timezone),
    ) == datetime(2026, 8, 10, 7, tzinfo=timezone)
    run_job.assert_not_called()


def test_create_scheduler_registers_a_catch_up_job() -> None:
    """A catch-up job uses the same proven scheduler trigger path."""
    job = PodcastCatchUpScheduledJob(
        id="podcast-catch-up",
        enabled=True,
        cron="0 8 * * *",
        task="podcast_catch_up",
        days=7,
        max_episodes=5,
    )
    scheduler = create_scheduler(
        ScheduleConfig(timezone="America/Detroit", jobs=(job,)),
        Mock(),
    )

    scheduled_jobs = scheduler.get_jobs()
    assert [scheduled_job.id for scheduled_job in scheduled_jobs] == [
        "podcast-catch-up"
    ]
    assert scheduled_jobs[0].args == (job,)


def test_create_scheduler_rejects_invalid_cron() -> None:
    """Official CronTrigger validation rejects an invalid expression."""
    config = ScheduleConfig(
        timezone="America/Detroit",
        jobs=(make_job("bad-cron", enabled=True, cron="not a cron"),),
    )

    with pytest.raises(ValueError):
        create_scheduler(config, Mock())


def test_run_until_stopped_shuts_down_when_it_is_asked_to() -> None:
    """A stop request starts and stops through APScheduler's own contract."""
    scheduler = Mock(spec=AsyncIOScheduler)

    async def drive() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        await run_until_stopped(scheduler, stop_event)

    asyncio.run(drive())

    scheduler.start.assert_called_once_with()
    scheduler.shutdown.assert_called_once_with(wait=True)


def test_every_job_runs_on_the_scheduler_s_own_event_loop() -> None:
    """The process keeps one event loop, and every job runs on it.

    Each firing used to get a loop of its own that was closed when the job
    ended, while the process went on sharing a model client across all of them.
    Anything a job left behind then belonged to a dead loop, and the next job
    died reaching into it. One loop for the process is what makes shared state
    safe to hold.
    """
    timezone = ZoneInfo("America/Detroit")
    config = ScheduleConfig(
        timezone="America/Detroit",
        jobs=(make_job("news", enabled=True, cron="0 7 * * mon-fri"),),
    )
    observed: list[object] = []

    async def record_running_loop(_job: WebResearchScheduledJob) -> None:
        observed.append(asyncio.get_running_loop())

    scheduler = create_scheduler(config, record_running_loop)

    async def drive() -> tuple[object, list[object]]:
        stop_event = asyncio.Event()
        serving = asyncio.create_task(run_until_stopped(scheduler, stop_event))
        while not scheduler.running:
            await asyncio.sleep(0)
        scheduler.get_job("news").modify(next_run_time=datetime.now(timezone))
        deadline = monotonic() + 5
        while not observed and monotonic() < deadline:
            await asyncio.sleep(0.01)
        stop_event.set()
        await serving
        return asyncio.get_running_loop(), observed

    serving_loop, ran_on = asyncio.run(drive())

    assert ran_on, "the job never ran"
    assert ran_on[0] is serving_loop
