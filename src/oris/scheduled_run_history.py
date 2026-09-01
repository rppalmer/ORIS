"""Read-only history of scheduled runs, for interfaces to list.

Separate from `scheduled_runs` on purpose. That module executes jobs and pulls
in LangGraph, the podcast workflow and the knowledge repository; an interface
that only wants to say what ran should carry none of it.

Deliberately not a filename-only listing, which is how `ThreatReportStore`
works. A run's status lives inside its record, and status is the reason to
look: a failed run deletes its report and leaves the record as its only trace.
Two failures sat unnoticed in this project's own history for three weeks
because nothing ever listed them. Records are small and few, so reading each
one costs nothing worth saving.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SHORT_ID_LENGTH = 8
"""How much of a run ID identifies it on screen.

Long enough to stay unique across far more runs than a person keeps, short
enough to retype. The full ID is always carried alongside it.
"""

DEFAULT_LIMIT = 20

DEFAULT_ROOT = Path("artifacts/scheduled")
"""Where scheduled runs are recorded, relative to the working directory.

Shared with `scheduled_runs`, which writes here, so a reader cannot drift from
a writer. Relative is not ideal -- a job started from elsewhere records
elsewhere -- but that is the service-owned-paths work, and splitting the
default in two while waiting for it would be the worse bug.
"""


@dataclass(frozen=True)
class ScheduledRun:
    """One attempt at one job, as an interface needs to show it."""

    run_id: str
    job_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    report_path: str | None
    task: str | None

    @property
    def short_id(self) -> str:
        """The handle a reader types to ask for this run."""
        return self.run_id[:SHORT_ID_LENGTH]

    @property
    def has_report(self) -> bool:
        """Whether there is anything to read, as opposed to only a record."""
        return self.report_path is not None

    @property
    def duration_seconds(self) -> float | None:
        """How long the attempt took, or None while it is still running."""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class ScheduledRunListing:
    """What one query returned, and whether that was all of it.

    `total` and `truncated` are part of the answer rather than something the
    caller recomputes. A listing that quietly stops at a limit is the failure
    this whole feature exists to avoid.
    """

    runs: tuple[ScheduledRun, ...]
    total: int
    truncated: bool
    job_id: str | None = None
    """Which job this was narrowed to, so an empty result can say why.

    Without it, "no runs" for one job renders identically to "no runs at all",
    and nine recorded runs read as none.
    """


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ScheduledRunHistory:
    """List the runs recorded under one artifact root."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @staticmethod
    def read_record(record: dict[str, Any], *, job_id: str) -> ScheduledRun:
        """Build one run from a stored record.

        Nothing here rejects a record for missing a field. This reads history,
        including history written by an older version of the writer, and a
        record that cannot be parsed is still proof that something ran.
        """
        report_path = record.get("report_path")
        return ScheduledRun(
            run_id=str(record.get("run_id") or "unknown"),
            job_id=str(record.get("job_id") or job_id),
            status=str(record.get("status") or "unknown"),
            started_at=_parse_time(record.get("started_at")),
            finished_at=_parse_time(record.get("finished_at")),
            error=record.get("error") if isinstance(record.get("error"), str) else None,
            report_path=report_path if isinstance(report_path, str) else None,
            task=record.get("task") if isinstance(record.get("task"), str) else None,
        )

    def _records(self) -> Iterator[ScheduledRun]:
        if not self.directory.is_dir():
            return
        for path in self.directory.glob("*/*.json"):
            job_id = path.parent.name
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = None
            if not isinstance(record, dict):
                # Surfaced rather than skipped: a record nobody can read is
                # still a run that happened, and dropping it would put the
                # reader back to trusting that the list is complete.
                yield ScheduledRun(
                    run_id=path.stem,
                    job_id=job_id,
                    status="unreadable",
                    started_at=None,
                    finished_at=None,
                    error=f"Could not read {path.name}.",
                    report_path=None,
                    task=None,
                )
                continue
            yield self.read_record(record, job_id=job_id)

    def recent(
        self,
        limit: int = DEFAULT_LIMIT,
        *,
        job_id: str | None = None,
    ) -> ScheduledRunListing:
        """Return runs newest first, saying how many there were in total."""
        if limit < 1:
            raise ValueError("Scheduled run limit must be at least one")
        runs = [
            run for run in self._records() if job_id is None or run.job_id == job_id
        ]
        # A record with no readable start time sorts last rather than raising.
        # It is the oldest kind of record there is, and losing the whole
        # listing to one of them would be the worse trade.
        runs.sort(
            key=lambda run: (run.started_at is not None, run.started_at),
            reverse=True,
        )
        return ScheduledRunListing(
            runs=tuple(runs[:limit]),
            total=len(runs),
            truncated=len(runs) > limit,
            job_id=job_id,
        )

    def job_ids(self) -> tuple[str, ...]:
        """Every job that has ever recorded a run, in name order."""
        if not self.directory.is_dir():
            return ()
        return tuple(
            sorted(path.name for path in self.directory.iterdir() if path.is_dir())
        )
