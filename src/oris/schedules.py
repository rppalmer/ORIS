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


ConfiguredScheduledJob = Annotated[
    WebResearchScheduledJob | YouTubeCatchUpScheduledJob,
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
