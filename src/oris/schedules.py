"""Validated configuration for scheduled ORIS jobs."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from oris.config import NonEmptyString
from oris.search import SearchCategory

JobId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]


class ScheduledJob(BaseModel):
    """Settings shared by every allowlisted scheduled job."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: JobId
    enabled: bool
    cron: NonEmptyString


class WebResearchScheduledJob(ScheduledJob):
    """One scheduled Web Research job."""

    task: Literal["web_research"]
    prompt: NonEmptyString
    date_window: Literal["previous_day"]
    search_category: SearchCategory


class YouTubeCatchUpScheduledJob(ScheduledJob):
    """One scheduled YouTube Catch-up job."""

    task: Literal["youtube_catch_up"]
    days: int
    max_videos: int


class PodcastCatchUpScheduledJob(ScheduledJob):
    """One scheduled Podcast Catch-up job.

    `max_episodes` is an ORIS budget rather than a Net-Razor one: Net-Razor caps
    per feed and cannot know what a single run can afford. It is stated here so
    the cost of a run is visible in the schedule file and in its history, and
    because it and the cron interval are related — a run that transcribes its
    whole budget can last a long time, and a job still running when its next
    firing is due has that firing skipped in silence.
    """

    task: Literal["podcast_catch_up"]
    days: int
    max_episodes: int


ConfiguredScheduledJob = Annotated[
    WebResearchScheduledJob | YouTubeCatchUpScheduledJob | PodcastCatchUpScheduledJob,
    Field(discriminator="task"),
]


class ScheduleConfig(BaseModel):
    """Complete project-owned schedule configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    timezone: NonEmptyString
    jobs: tuple[ConfiguredScheduledJob, ...] = ()

    @field_validator("timezone")
    @classmethod
    def require_known_timezone(cls, value: str) -> str:
        """Reject time zones unavailable to the local runtime."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"Unknown timezone: {value}") from error
        return value

    @model_validator(mode="after")
    def require_unique_job_ids(self) -> Self:
        """Keep job identifiers unambiguous."""
        job_ids = [job.id for job in self.jobs]
        duplicate_ids = sorted(
            job_id for job_id in set(job_ids) if job_ids.count(job_id) > 1
        )
        if duplicate_ids:
            raise ValueError(f"Duplicate job IDs: {', '.join(duplicate_ids)}")
        return self


def load_schedule_config(path: Path = Path("schedules.toml")) -> ScheduleConfig:
    """Load and validate a TOML schedule file."""
    with path.open("rb") as schedule_file:
        values = tomllib.load(schedule_file)
    return ScheduleConfig.model_validate(values)
