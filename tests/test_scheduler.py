"""Tests for the local APScheduler runtime."""

from datetime import datetime
from threading import Event
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

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


def test_run_until_stopped_waits_for_active_jobs_on_shutdown() -> None:
    """A stop request uses APScheduler's graceful shutdown contract."""
    scheduler = Mock(spec=BackgroundScheduler)
    stop_event = Event()
    stop_event.set()

    run_until_stopped(scheduler, stop_event)

    scheduler.start.assert_called_once_with()
    scheduler.shutdown.assert_called_once_with(wait=True)
