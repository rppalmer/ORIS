"""Tests for durable Threat Intel evidence storage and its retention window."""

from datetime import UTC, datetime, timedelta

import pytest

from oris.threat_reports import ThreatReportStore

THREAD = "3f2c1e0b-7a41-4c9d-9b2e-15d4a6c8f0e2"
EVIDENCE = {"8.8.8.8": {"data": {"sources": {"virustotal": {"ok": True}}}}}


def test_saved_report_round_trips_by_id(tmp_path) -> None:
    """The ID printed in chat is the whole handle needed to read it back."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save("enrich 8.8.8.8", EVIDENCE, thread_id=THREAD)
    document = store.load(stored.report_id)

    assert document is not None
    assert document["request"] == "enrich 8.8.8.8"
    assert document["evidence"] == EVIDENCE


def test_report_is_self_describing_on_disk(tmp_path) -> None:
    """A report copied elsewhere still says what it was and when."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save("enrich 8.8.8.8", EVIDENCE, thread_id=THREAD)

    assert stored.report_id in stored.path.name
    assert "8.8.8.8" in stored.path.name
    assert stored.path.read_text(encoding="utf-8").startswith("{")


def test_unsafe_characters_in_a_request_cannot_escape_the_directory(tmp_path) -> None:
    """A URL indicator contains slashes; a filename must not."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save(
        "enrich http://evil.test/../../etc/passwd", EVIDENCE, thread_id=THREAD
    )

    assert stored.path.parent == tmp_path
    assert "/" not in stored.path.name.removesuffix(".json")


def test_retention_removes_reports_past_the_window(tmp_path) -> None:
    """Every report is kept forever otherwise, and they accumulate silently."""
    # Written through a store that never expires anything, so the setup does not
    # exercise the behaviour under test.
    setup = ThreatReportStore(tmp_path, retention_days=36500)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    old = setup.save(
        "enrich 1.1.1.1", EVIDENCE, thread_id=THREAD, now=now - timedelta(days=8)
    )
    edge = setup.save(
        "enrich 2.2.2.2", EVIDENCE, thread_id=THREAD, now=now - timedelta(days=6)
    )
    fresh = setup.save("enrich 3.3.3.3", EVIDENCE, thread_id=THREAD, now=now)

    removed = ThreatReportStore(tmp_path, retention_days=7).prune(now=now)

    assert removed == 1
    assert not old.path.exists()
    assert edge.path.exists()
    assert fresh.path.exists()


def test_saving_prunes_so_retention_needs_no_scheduler(tmp_path) -> None:
    """Writing is exactly when the directory is known to have changed."""
    store = ThreatReportStore(tmp_path, retention_days=7)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    stale = store.save(
        "enrich 1.1.1.1", EVIDENCE, thread_id=THREAD, now=now - timedelta(days=30)
    )

    store.save("enrich 4.4.4.4", EVIDENCE, thread_id=THREAD, now=now)

    assert not stale.path.exists()


def test_retention_reads_age_from_the_name_not_the_mtime(tmp_path) -> None:
    """Backups and sync clients rewrite mtime, which would resurrect old reports."""
    store = ThreatReportStore(tmp_path, retention_days=7)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    stale = store.save(
        "enrich 1.1.1.1", EVIDENCE, thread_id=THREAD, now=now - timedelta(days=30)
    )
    # Touch it, as a sync client would.
    stale.path.touch()

    assert store.prune(now=now) == 1
    assert not stale.path.exists()


def test_loading_a_missing_report_returns_none(tmp_path) -> None:
    """An expired ID must report absence, not raise at the prompt."""
    store = ThreatReportStore(tmp_path, retention_days=30)

    assert store.load("abcdef") is None
    assert store.load("") is None


@pytest.mark.parametrize("wildcard", ["*", "?????1", "[a-f]bcdef", "*cdef"])
def test_a_wildcard_is_matched_rather_than_interpreted(tmp_path, wildcard) -> None:
    """`/threat show` takes an ID from the user and looks it up on disk.

    Report IDs are matched by filename glob, where `*`, `?`, and `[…]` are
    patterns rather than characters, so an ID containing one opened whichever
    report it happened to match instead of reporting an unknown ID. The report
    it opens is the user's own, so this reads as a convenience rather than as a
    bug — which is exactly why it would go unnoticed.
    """
    store = ThreatReportStore(tmp_path, retention_days=36500)
    stored = store.save("enrich 1.1.1.1", EVIDENCE, thread_id=THREAD)

    assert store.load(wildcard) is None
    # The real ID still resolves, so the check is not simply refusing everything.
    assert store.load(stored.report_id) is not None


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
    store.save(
        "enrich 1.1.1.1", EVIDENCE, thread_id=THREAD, now=now - timedelta(days=2)
    )
    store.save("enrich 2.2.2.2", EVIDENCE, thread_id=THREAD, now=now)

    assert store.latest()["request"] == "enrich 2.2.2.2"


def test_latest_is_none_before_anything_is_stored(tmp_path) -> None:
    assert ThreatReportStore(tmp_path, retention_days=30).latest() is None


def test_recent_lists_stored_reports_newest_first(tmp_path) -> None:
    """An interface offering evidence has to know what evidence exists."""
    store = ThreatReportStore(tmp_path, retention_days=36500)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    older = store.save(
        "enrich 1.1.1.1", EVIDENCE, thread_id=THREAD, now=now - timedelta(days=2)
    )
    newer = store.save("enrich 2.2.2.2", EVIDENCE, thread_id=THREAD, now=now)
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")

    listed = store.recent()

    assert [report.report_id for report in listed] == [
        newer.report_id,
        older.report_id,
    ]
    assert listed[0].created_at == now
    assert store.recent(limit=1) == listed[:1]


def test_recent_is_empty_before_anything_is_stored(tmp_path) -> None:
    assert ThreatReportStore(tmp_path / "missing", retention_days=30).recent() == []


def test_deleting_a_conversation_takes_only_its_own_evidence(tmp_path) -> None:
    """Deleting a conversation has to reach the evidence it collected.

    These files hold every indicator investigated and everything the providers
    returned about them, kept for a month. Someone deleting the conversation
    means those too — and just as importantly, means only those.
    """
    store = ThreatReportStore(tmp_path, retention_days=36500)
    other_thread = "9c4d2f1a-0b83-4e56-8a17-2d6f9e0b4c31"
    mine = store.save("enrich 1.1.1.1", EVIDENCE, thread_id=THREAD)
    also_mine = store.save("enrich 2.2.2.2", EVIDENCE, thread_id=THREAD)
    theirs = store.save("enrich 3.3.3.3", EVIDENCE, thread_id=other_thread)

    removed = store.delete_for_thread(THREAD)

    assert removed == 2
    assert not mine.path.exists()
    assert not also_mine.path.exists()
    assert theirs.path.exists()


def test_a_report_names_the_conversation_in_its_name_and_its_header(tmp_path) -> None:
    """Both, because each answers a question the other cannot.

    The filename is what lets deletion and listing work without opening a file
    or keeping an index that could fall out of step. The header is what a
    report still says about itself once it has been copied somewhere else.
    """
    store = ThreatReportStore(tmp_path, retention_days=30)

    stored = store.save("enrich 8.8.8.8", EVIDENCE, thread_id=THREAD)

    assert stored.path.name.endswith(f"-{THREAD}.json")
    assert stored.thread_id == THREAD
    assert store.load(stored.report_id)["thread_id"] == THREAD


def test_a_request_full_of_separators_still_leaves_the_thread_readable(
    tmp_path,
) -> None:
    """The filename is split on `-`, so no other field may contain one.

    Indicators and CVE identifiers are full of hyphens, and a request that
    smuggled one into the subject would make the trailing field ambiguous —
    deletion would then either miss the report or take someone else's.
    """
    store = ThreatReportStore(tmp_path, retention_days=36500)

    stored = store.save("ref CVE-2024-3400 and 1.1.1.1", EVIDENCE, thread_id=THREAD)

    # Timestamp, ID, subject: three fields before the thread, so splitting on
    # the separator has to leave exactly one piece of subject behind.
    leading = stored.path.name.removesuffix(f"-{THREAD}.json").split("-")
    assert leading[2:] == ["ref_CVE_2024_3400_and_1.1.1.1"]
    assert store.recent()[0].thread_id == THREAD
    assert store.delete_for_thread(THREAD) == 1


def test_a_report_stored_without_a_conversation_is_not_deleted_by_one(
    tmp_path,
) -> None:
    """A run outside any conversation must not be swept up by an unrelated one.

    Reports written before the thread was recorded parse with no thread at all,
    and a scheduled or directly invoked run has none to give. Neither should
    answer to a real conversation's deletion.
    """
    store = ThreatReportStore(tmp_path, retention_days=36500)
    orphan = store.save("enrich 1.1.1.1", EVIDENCE, thread_id="")
    legacy = tmp_path / "20260811T104233Z-a3f21c-45.83.192.4.json"
    legacy.write_text("{}", encoding="utf-8")

    assert store.delete_for_thread(THREAD) == 0
    assert orphan.path.exists()
    assert legacy.exists()
    # Still listed, so it still ages out rather than living here forever.
    assert legacy in {report.path for report in store.recent()}
