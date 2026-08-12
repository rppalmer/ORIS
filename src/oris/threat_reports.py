"""Durable storage for full Threat Intel evidence.

The pivoted report goes to the conversation; the complete provider responses are
too large for that and land here instead. Everything needed to find, age out, or
identify a report lives in its filename, so there is no index to fall out of step
with the files themselves.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

REPORT_ID_LENGTH = 6
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
MAX_SUBJECT_LENGTH = 40
_UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")
# 20260811T104233Z-a3f21c-45.83.192.4.json
_REPORT_NAME = re.compile(
    rf"^(?P<timestamp>\d{{8}}T\d{{6}}Z)-(?P<id>[0-9a-f]{{{REPORT_ID_LENGTH}}})-"
    r"(?P<subject>.*)\.json$"
)


@dataclass(frozen=True)
class ThreatReport:
    """One stored evidence report."""

    report_id: str
    path: Path
    created_at: datetime


def _subject_slug(request: str) -> str:
    """Make a request safe for a filename without losing what it was about."""
    slug = _UNSAFE_CHARACTERS.sub("_", request.strip()).strip("_")
    return slug[:MAX_SUBJECT_LENGTH] or "request"


class ThreatReportStore:
    """Write, find, and age out full evidence reports on disk."""

    def __init__(self, directory: Path, retention_days: int) -> None:
        if retention_days < 1:
            raise ValueError("Threat report retention must be at least one day")
        self.directory = directory
        self.retention_days = retention_days

    def save(
        self,
        request: str,
        evidence: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> ThreatReport:
        """Store one report and age out anything past the retention window."""
        created_at = now or datetime.now(UTC)
        report_id = uuid4().hex[:REPORT_ID_LENGTH]
        name = (
            f"{created_at.strftime(TIMESTAMP_FORMAT)}-{report_id}"
            f"-{_subject_slug(request)}.json"
        )
        path = self.directory / name
        self.directory.mkdir(parents=True, exist_ok=True)
        # The header makes the file self-describing, so a report that has been
        # copied somewhere else still says what it was and when.
        document = {
            "report_id": report_id,
            "request": request,
            "created_at": created_at.isoformat(),
            "evidence": evidence,
        }
        temporary_path = path.with_name(f".{path.name}.tmp")
        temporary_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)

        self.prune(now=created_at)
        return ThreatReport(report_id=report_id, path=path, created_at=created_at)

    def load(self, report_id: str) -> dict[str, Any] | None:
        """Return one stored report by ID, or None when it is gone."""
        path = self._path_for(report_id)
        if path is None:
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def prune(self, *, now: datetime | None = None) -> int:
        """Delete reports older than the retention window and return the count.

        The age comes from the name rather than the file's mtime: a backup,
        editor, or sync client rewrites mtime, which would quietly keep expired
        reports alive forever.
        """
        if not self.directory.is_dir():
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        removed = 0
        for path in self.directory.glob("*.json"):
            created_at = _created_at(path)
            if created_at is None or created_at >= cutoff:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def latest(self) -> dict[str, Any] | None:
        """Return the most recent stored report.

        `/threat show` with no ID means "the one I just ran", which is what a
        reader asking for detail almost always wants.
        """
        newest = self._newest_path()
        if newest is None:
            return None
        try:
            document = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def _newest_path(self) -> Path | None:
        if not self.directory.is_dir():
            return None
        dated = [
            (created_at, path)
            for path in self.directory.glob("*.json")
            if (created_at := _created_at(path)) is not None
        ]
        return max(dated)[1] if dated else None

    def _path_for(self, report_id: str) -> Path | None:
        wanted = report_id.strip().casefold()
        if not wanted or not self.directory.is_dir():
            return None
        matches = sorted(self.directory.glob(f"*-{wanted}-*.json"))
        return matches[0] if len(matches) == 1 else None


def _created_at(path: Path) -> datetime | None:
    """Read a report's creation time back out of its filename."""
    match = _REPORT_NAME.match(path.name)
    if match is None:
        return None
    try:
        stamp = datetime.strptime(match.group("timestamp"), TIMESTAMP_FORMAT)
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC)
