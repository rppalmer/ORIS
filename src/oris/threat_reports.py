"""Durable storage for full Threat Intel evidence.

The pivoted report goes to the conversation; the complete provider responses are
too large for that and land here instead. Everything needed to find, age out,
identify, or delete a report lives in its filename, so there is no index to fall
out of step with the files themselves.
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
TIMESTAMP_RESOLUTION_SECONDS = 1
"""How much earlier than the truth a filename timestamp can read.

Whole seconds, so a report written a fraction of a second into a run dates from
just before it started. Anything comparing the two has to allow for that.
"""

MAX_SUBJECT_LENGTH = 40
MAX_THREAD_LENGTH = 64
UNKNOWN_THREAD = "no_session"
# `-` separates the fields, so no field but the last may contain one. The
# subject is the free-form one, and it is already being flattened; losing a
# hyphen to an underscore costs nothing next to a name that splits without
# guessing. The thread is last, so it keeps the hyphens a session ID is made of
# and stays comparable to the ID the rest of the application holds.
_UNSAFE_IN_SUBJECT = re.compile(r"[^A-Za-z0-9._]+")
_UNSAFE_IN_THREAD = re.compile(r"[^A-Za-z0-9._-]+")
# What `save` generates, and therefore the only thing worth looking for.
_REPORT_ID = re.compile(rf"^[0-9a-f]{{{REPORT_ID_LENGTH}}}$")
# 20260811T104233Z-a3f21c-45.83.192.4-6f2c1e0b-...-9d4a.json
#
# The thread is last and is the only field allowed to contain `-`, so the split
# needs no assumption about what a session ID looks like. The group is optional
# so that reports written before the thread was recorded still parse, and so
# still age out on schedule rather than living forever.
_REPORT_NAME = re.compile(
    rf"^(?P<timestamp>\d{{8}}T\d{{6}}Z)-(?P<id>[0-9a-f]{{{REPORT_ID_LENGTH}}})"
    r"-(?P<subject>[^-]*)(?:-(?P<thread>.+))?\.json$"
)


@dataclass(frozen=True)
class ThreatReport:
    """One stored evidence report."""

    report_id: str
    path: Path
    created_at: datetime
    thread_id: str | None


def _subject_slug(request: str) -> str:
    """Make a request safe for a filename without losing what it was about."""
    slug = _UNSAFE_IN_SUBJECT.sub("_", request.strip()).strip("_")
    return slug[:MAX_SUBJECT_LENGTH] or "request"


def _thread_slug(thread_id: str) -> str:
    """Name the conversation in a way the rest of the application recognises.

    A session ID is a UUID, so this normally changes nothing; the substitution
    is here because a filename must not be shaped by whatever produced the ID.
    """
    slug = _UNSAFE_IN_THREAD.sub("_", thread_id.strip()).strip("_-")
    return slug[:MAX_THREAD_LENGTH] or UNKNOWN_THREAD


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
        thread_id: str,
        now: datetime | None = None,
    ) -> ThreatReport:
        """Store one report and age out anything past the retention window.

        The conversation that asked for the evidence is recorded because these
        are the most sensitive files ORIS writes — the indicators investigated
        and everything the providers said about them — and deleting that
        conversation has to be able to take them with it.
        """
        created_at = now or datetime.now(UTC)
        report_id = uuid4().hex[:REPORT_ID_LENGTH]
        thread = _thread_slug(thread_id)
        name = (
            f"{created_at.strftime(TIMESTAMP_FORMAT)}-{report_id}"
            f"-{_subject_slug(request)}-{thread}.json"
        )
        path = self.directory / name
        self.directory.mkdir(parents=True, exist_ok=True)
        # The header makes the file self-describing, so a report that has been
        # copied somewhere else still says what it was, when, and for whom.
        document = {
            "report_id": report_id,
            "thread_id": thread,
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
        return ThreatReport(
            report_id=report_id,
            path=path,
            created_at=created_at,
            thread_id=thread,
        )

    def delete_for_thread(self, thread_id: str) -> int:
        """Delete every report a conversation produced and return the count.

        Matched on the filename's last field, which is exactly the thread and
        nothing else, so this never has to open a file or consult an index.
        """
        thread = _thread_slug(thread_id)
        removed = 0
        for report in self._stored():
            if report.thread_id != thread:
                continue
            report.path.unlink(missing_ok=True)
            removed += 1
        return removed

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

    def recent(self, limit: int = 50) -> list[ThreatReport]:
        """List stored reports, newest first, without reading their contents.

        An interface that offers evidence has to know what evidence exists. The
        filename carries the ID, the time, and the conversation, so listing
        never opens a file.
        """
        reports = sorted(
            self._stored(), key=lambda report: report.created_at, reverse=True
        )
        return reports[:limit]

    def _stored(self) -> list[ThreatReport]:
        """Every report whose filename still parses."""
        if not self.directory.is_dir():
            return []
        reports = []
        for path in self.directory.glob("*.json"):
            match = _REPORT_NAME.match(path.name)
            created_at = _created_at(path)
            if match is None or created_at is None:
                continue
            reports.append(
                ThreatReport(
                    report_id=match.group("id"),
                    path=path,
                    created_at=created_at,
                    thread_id=match.group("thread"),
                )
            )
        return reports

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
        """Find one report by the ID printed in chat.

        The ID is checked against the shape it is generated in before it
        reaches `glob`, which reads `*`, `?`, and `[…]` as patterns rather than
        as characters: `/threat show *` otherwise opened whichever report the
        wildcard happened to match instead of saying the ID is unknown.
        """
        wanted = report_id.strip().casefold()
        if not _REPORT_ID.match(wanted) or not self.directory.is_dir():
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
