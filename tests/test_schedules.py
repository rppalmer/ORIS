"""Tests for project-owned schedule configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oris.schedules import load_schedule_config


def test_project_schedule_file_contains_approved_weekday_job() -> None:
    """The committed file retains the approved weekday research job."""
    project_root = Path(__file__).parents[1]

    config = load_schedule_config(project_root / "schedules.toml")
    job = next(job for job in config.jobs if job.id == "weekday-ai-news")

    assert config.timezone == "America/Detroit"
    assert job.enabled is True
    assert job.cron == "0 7 * * mon-fri"
    assert job.task == "web_research"
    assert job.prompt == (
        "Research the most important AI-agent developments from yesterday."
    )
    assert job.date_window == "previous_day"
    assert job.search_category == "news"


def test_load_schedule_config_accepts_an_allowlisted_job(tmp_path: Path) -> None:
    """A valid Web Research job is parsed into immutable settings."""
    schedule_file = tmp_path / "schedules.toml"
    schedule_file.write_text(
        """
timezone = "America/Detroit"

[[jobs]]
id = "weekday-ai-news"
enabled = false
cron = "0 7 * * mon-fri"
task = "web_research"
prompt = "Research important AI-agent developments from yesterday."
date_window = "previous_day"
search_category = "news"
""".strip()
    )

    config = load_schedule_config(schedule_file)

    assert config.jobs[0].id == "weekday-ai-news"
    assert config.jobs[0].task == "web_research"
    assert config.jobs[0].enabled is False
    assert config.jobs[0].date_window == "previous_day"
    assert config.jobs[0].search_category == "news"


def test_schedule_config_rejects_duplicate_job_ids(tmp_path: Path) -> None:
    """Every job must have an unambiguous identifier."""
    schedule_file = tmp_path / "schedules.toml"
    schedule_file.write_text(
        """
timezone = "America/Detroit"

[[jobs]]
id = "daily-news"
enabled = false
cron = "0 7 * * *"
task = "web_research"
prompt = "First prompt."
date_window = "previous_day"
search_category = "news"

[[jobs]]
id = "daily-news"
enabled = false
cron = "0 8 * * *"
task = "web_research"
prompt = "Second prompt."
date_window = "previous_day"
search_category = "news"
""".strip()
    )

    with pytest.raises(ValidationError, match="Duplicate job IDs: daily-news"):
        load_schedule_config(schedule_file)


def test_schedule_config_rejects_unknown_timezone(tmp_path: Path) -> None:
    """Schedule calculations require an explicit known time zone."""
    schedule_file = tmp_path / "schedules.toml"
    schedule_file.write_text('timezone = "Not/A-Time-Zone"')

    with pytest.raises(ValidationError, match="Unknown timezone"):
        load_schedule_config(schedule_file)


def test_schedule_config_rejects_unapproved_tasks(tmp_path: Path) -> None:
    """Configuration cannot name arbitrary executables or callables."""
    schedule_file = tmp_path / "schedules.toml"
    schedule_file.write_text(
        """
timezone = "America/Detroit"

[[jobs]]
id = "unsafe-task"
enabled = false
cron = "0 7 * * *"
task = "shell_command"
prompt = "Do something."
date_window = "previous_day"
search_category = "news"
""".strip()
    )

    with pytest.raises(ValidationError, match="web_research"):
        load_schedule_config(schedule_file)


def test_a_podcast_job_states_its_own_run_budget(tmp_path) -> None:
    """A podcast job's cost is visible in the file rather than in the code."""
    schedule_file = tmp_path / "schedules.toml"
    schedule_file.write_text(
        'timezone = "America/Detroit"\n\n'
        "[[jobs]]\n"
        'id = "nightly-podcasts"\n'
        "enabled = true\n"
        'cron = "0 6 * * *"\n'
        'task = "podcast_catch_up"\n'
        "days = 1\n"
        "max_episodes = 5\n",
        encoding="utf-8",
    )

    config = load_schedule_config(schedule_file)

    job = config.jobs[0]
    assert job.task == "podcast_catch_up"
    assert job.days == 1
    assert job.max_episodes == 5
