"""Tests for durable Threat Intel evidence storage and its retention window."""

from datetime import UTC, datetime, timedelta

import pytest

from oris.threat_reports import ThreatReportStore

EVIDENCE = {"8.8.8.8": {"data": {"sources": {"virustotal": {"ok": True}}}}}


def test_saved_report_round_trips_by_id(tmp_path) -> None:
    """The ID printed in chat is the whole handle needed to read it back."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save("enrich 8.8.8.8", EVIDENCE)
    document = store.load(stored.report_id)

    assert document is not None
    assert document["request"] == "enrich 8.8.8.8"
    assert document["evidence"] == EVIDENCE


def test_report_is_self_describing_on_disk(tmp_path) -> None:
    """A report copied elsewhere still says what it was and when."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save("enrich 8.8.8.8", EVIDENCE)

    assert stored.report_id in stored.path.name
    assert "8.8.8.8" in stored.path.name
    assert stored.path.read_text(encoding="utf-8").startswith("{")


def test_unsafe_characters_in_a_request_cannot_escape_the_directory(tmp_path) -> None:
    """A URL indicator contains slashes; a filename must not."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save("enrich http://evil.test/../../etc/passwd", EVIDENCE)

    assert stored.path.parent == tmp_path
    assert "/" not in stored.path.name.removesuffix(".json")


def test_retention_removes_reports_past_the_window(tmp_path) -> None:
    """Every report is kept forever otherwise, and they accumulate silently."""
    # Written through a store that never expires anything, so the setup does not
    # exercise the behaviour under test.
    setup = ThreatReportStore(tmp_path, retention_days=36500)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    old = setup.save("enrich 1.1.1.1", EVIDENCE, now=now - timedelta(days=8))
    edge = setup.save("enrich 2.2.2.2", EVIDENCE, now=now - timedelta(days=6))
    fresh = setup.save("enrich 3.3.3.3", EVIDENCE, now=now)

    removed = ThreatReportStore(tmp_path, retention_days=7).prune(now=now)

    assert removed == 1
    assert not old.path.exists()
    assert edge.path.exists()
    assert fresh.path.exists()


def test_saving_prunes_so_retention_needs_no_scheduler(tmp_path) -> None:
    """Writing is exactly when the directory is known to have changed."""
    store = ThreatReportStore(tmp_path, retention_days=7)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    stale = store.save("enrich 1.1.1.1", EVIDENCE, now=now - timedelta(days=30))

    store.save("enrich 4.4.4.4", EVIDENCE, now=now)

    assert not stale.path.exists()


def test_retention_reads_age_from_the_name_not_the_mtime(tmp_path) -> None:
    """Backups and sync clients rewrite mtime, which would resurrect old reports."""
    store = ThreatReportStore(tmp_path, retention_days=7)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    stale = store.save("enrich 1.1.1.1", EVIDENCE, now=now - timedelta(days=30))
    # Touch it, as a sync client would.
    stale.path.touch()

    assert store.prune(now=now) == 1
    assert not stale.path.exists()


def test_loading_a_missing_report_returns_none(tmp_path) -> None:
    """An expired ID must report absence, not raise at the prompt."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    assert store.load("abcdef") is None
    assert store.load("") is None


def test_unrelated_files_in_the_directory_are_left_alone(tmp_path) -> None:
    """Retention only understands its own names; it must not delete anything else."""
    store = ThreatReportStore(tmp_path, retention_days=1)
    store.directory.mkdir(parents=True, exist_ok=True)
    stray = tmp_path / "notes.json"
    stray.write_text("{}", encoding="utf-8")

    store.prune(now=datetime(2030, 1, 1, tzinfo=UTC))

    assert stray.exists()


def test_retention_must_be_at_least_one_day(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least one day"):
        ThreatReportStore(tmp_path, retention_days=0)


def test_latest_returns_the_most_recent_report(tmp_path) -> None:
    """`/threat show` with no ID means the one just run."""
    store = ThreatReportStore(tmp_path, retention_days=36500)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    store.save("enrich 1.1.1.1", EVIDENCE, now=now - timedelta(days=2))
    store.save("enrich 2.2.2.2", EVIDENCE, now=now)

    assert store.latest()["request"] == "enrich 2.2.2.2"


def test_latest_is_none_before_anything_is_stored(tmp_path) -> None:
    assert ThreatReportStore(tmp_path, retention_days=30).latest() is None
