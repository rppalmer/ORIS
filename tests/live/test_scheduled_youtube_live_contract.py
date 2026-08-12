"""Opt-in end-to-end evaluation for scheduled YouTube Catch-up."""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from oris.scheduled_runs import run_scheduled_job
from oris.schedules import YouTubeCatchUpScheduledJob

LIVE_SCHEDULED_YOUTUBE_ENABLED = (
    os.environ.get("ORIS_RUN_LIVE_SCHEDULED_YOUTUBE_TESTS") == "1"
)
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_SCHEDULED_YOUTUBE_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_SCHEDULED_YOUTUBE_TESTS=1 to contact "
        "Net-Razor, YouTube, and oMLX and mark summarized videos processed."
    ),
)
def test_scheduled_youtube_retains_one_live_report() -> None:
    """The configured services complete one retained scheduled deliverable."""
    from oris.web_research_app import (
        build_youtube_catch_up_preparation,
        knowledge_repository,
        web_research_graph,
    )

    job = YouTubeCatchUpScheduledJob(
        id="youtube-catch-up-live",
        enabled=True,
        cron="0 8 * * *",
        task="youtube_catch_up",
        days=30,
        max_videos=1,
    )
    record = run_scheduled_job(
        job,
        web_research_graph,
        knowledge_repository,
        current_date=datetime.now(ZoneInfo("America/Detroit")).date(),
        build_youtube_catch_up=build_youtube_catch_up_preparation,
    )

    assert record.status == "succeeded"
    assert record.report_path is not None
    report_path = PROJECT_ROOT / "artifacts" / "scheduled" / record.report_path
    report = report_path.read_text(encoding="utf-8")
    assert "# Scheduled YouTube catch-up: youtube-catch-up-live" in report
    assert "## Digest" in report
    assert "## Videos" in report
    assert "## Sources" in report
    assert "## Caveats" in report
    assert "transcript_call_ids" not in report
    print(f"Scheduled YouTube report: {report_path}")
