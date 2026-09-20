"""Project-owned local scheduler for configured ORIS jobs."""

import argparse
import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from oris.scheduled_runs import run_scheduled_job_async
from oris.schedules import (
    DEFAULT_SCHEDULE_FILE,
    ScheduleConfig,
    ScheduledJob,
    load_schedule_config,
)

logger = logging.getLogger(__name__)


def create_scheduler(
    config: ScheduleConfig,
    run_job: Callable[[ScheduledJob], Awaitable[None]],
) -> AsyncIOScheduler:
    """Create an in-memory scheduler containing only enabled configured jobs.

    Jobs are coroutines and run on the loop this scheduler is started from, so
    the process keeps one event loop for its whole life. That is what lets it
    hold a model client across jobs: a connection pooled by one job belongs to
    the loop that opened it, and a loop that closes when a job ends leaves the
    next job reaching into a dead one. See the history entry for 2026-09-19.
    """
    timezone = ZoneInfo(config.timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)

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


async def run_until_stopped(
    scheduler: AsyncIOScheduler,
    stop_event: asyncio.Event,
) -> None:
    """Run on the caller's loop and wait for a graceful shutdown request.

    A job still running when the stop arrives is cancelled rather than waited
    for. That is the asyncio executor's contract, and it is what a restart
    wants: the old behaviour blocked for as long as the job took, which for an
    overnight catch-up meant launchd killing the process anyway.
    """
    scheduler.start()
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=True)
        # The scheduler puts its own shutdown back on this loop, so give it
        # the turn it needs before the loop goes away.
        await asyncio.sleep(0)


async def serve(scheduler: AsyncIOScheduler) -> None:
    """Stop on a signal, using handlers this loop owns."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(number, stop_event.set)
    await run_until_stopped(scheduler, stop_event)


def main() -> None:
    """Run configured ORIS jobs on their project-owned schedules."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "--schedule-file",
        type=Path,
        default=DEFAULT_SCHEDULE_FILE,
        help="Schedule configuration path (default: schedules.toml)",
    )
    args = parser.parse_args()

    config = load_schedule_config(args.schedule_file)
    timezone = ZoneInfo(config.timezone)

    from oris.web_research_app import (
        build_podcast_catch_up_preparation,
        knowledge_repository,
        read_state_store,
        web_research_graph,
    )

    async def execute_job(job: ScheduledJob) -> None:
        record = await run_scheduled_job_async(
            job,
            web_research_graph,
            knowledge_repository,
            read_state_store,
            current_date=datetime.now(timezone).date(),
            build_podcast_catch_up=build_podcast_catch_up_preparation,
        )
        logger.info("Scheduled run succeeded: %s", record.report_path)

    scheduler = create_scheduler(config, execute_job)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "Starting scheduler with %d enabled job(s) in %s",
        len(scheduler.get_jobs()),
        config.timezone,
    )
    asyncio.run(serve(scheduler))
    logger.info("Scheduler stopped")
