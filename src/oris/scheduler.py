"""Project-owned local scheduler for configured ORIS jobs."""

import argparse
import logging
import signal
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event
from types import FrameType
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from oris.scheduled_runs import run_scheduled_job
from oris.schedules import ScheduleConfig, ScheduledJob, load_schedule_config

logger = logging.getLogger(__name__)


def create_scheduler(
    config: ScheduleConfig,
    run_job: Callable[[ScheduledJob], None],
) -> BackgroundScheduler:
    """Create an in-memory scheduler containing only enabled configured jobs."""
    timezone = ZoneInfo(config.timezone)
    scheduler = BackgroundScheduler(timezone=timezone)

    for job in config.jobs:
        if not job.enabled:
            continue
        scheduler.add_job(
            run_job,
            trigger=CronTrigger.from_crontab(job.cron, timezone=timezone),
            args=(job,),
            id=job.id,
            name=job.id,
            coalesce=True,
            max_instances=1,
        )

    return scheduler


def run_until_stopped(
    scheduler: BackgroundScheduler,
    stop_event: Event,
) -> None:
    """Run in the background and wait for a graceful shutdown request."""
    scheduler.start()
    try:
        stop_event.wait()
    finally:
        scheduler.shutdown(wait=True)


def main() -> None:
    """Run configured ORIS jobs on their project-owned schedules."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "--schedule-file",
        type=Path,
        default=Path("schedules.toml"),
        help="Schedule configuration path (default: schedules.toml)",
    )
    args = parser.parse_args()

    config = load_schedule_config(args.schedule_file)
    timezone = ZoneInfo(config.timezone)

    from oris.web_research_app import (
        build_podcast_catch_up_preparation,
        knowledge_repository,
        web_research_graph,
    )

    def execute_job(job: ScheduledJob) -> None:
        record = run_scheduled_job(
            job,
            web_research_graph,
            knowledge_repository,
            current_date=datetime.now(timezone).date(),
            build_podcast_catch_up=build_podcast_catch_up_preparation,
        )
        logger.info("Scheduled run succeeded: %s", record.report_path)

    scheduler = create_scheduler(config, execute_job)
    stop_event = Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "Starting scheduler with %d enabled job(s) in %s",
        len(scheduler.get_jobs()),
        config.timezone,
    )
    run_until_stopped(scheduler, stop_event)
    logger.info("Scheduler stopped")
