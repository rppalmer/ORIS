"""Tests for reading the durable history of scheduled runs."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oris.scheduled_run_history import ScheduledRunHistory


def write_record(
    root: Path,
    job_id: str,
    started: str,
    run_id: str,
    *,
    status: str = "succeeded",
    finished: str | None = None,
    error: str | None = None,
    with_report: bool = True,
) -> None:
    """Write one run record the way `scheduled_runs` writes it."""
    directory = root / job_id
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{started.replace('-', '').replace(':', '')[:15]}Z-{run_id}"
    report_name = f"{stem}.md"
    record = {
        "job_id": job_id,
        "run_id": run_id,
        "started_at": started,
        "status": status,
        "finished_at": finished,
        "report_path": f"{job_id}/{report_name}" if with_report else None,
        "error": error,
        "task": "web_research",
    }
    (directory / f"{stem}.json").write_text(json.dumps(record), encoding="utf-8")
    if with_report:
        (directory / report_name).write_text("# report\n", encoding="utf-8")


def test_history_lists_every_job_newest_first(tmp_path: Path) -> None:
    """One listing covers every job, because a run is a run."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "aaaaaaaa-1111")
    write_record(tmp_path, "podcasts", "2026-08-31T03:00:00Z", "bbbbbbbb-2222")
    write_record(tmp_path, "news", "2026-08-29T07:00:00Z", "cccccccc-3333")

    listing = ScheduledRunHistory(tmp_path).recent()

    assert [run.job_id for run in listing.runs] == ["podcasts", "news", "news"]
    assert listing.total == 3
    assert listing.truncated is False


def test_history_keeps_failed_runs_and_their_reason(tmp_path: Path) -> None:
    """A failed run deletes its report, so the record is the only trace.

    Two runs failed in this project's own history and stayed invisible for
    three weeks, because nothing in ORIS ever listed them. Hiding a failure
    behind "has no report to show" would rebuild exactly that blind spot.
    """
    write_record(
        tmp_path,
        "news",
        "2026-08-18T11:00:00Z",
        "dddddddd-4444",
        status="failed",
        error="APIConnectionError: Connection error.",
        with_report=False,
    )

    run = ScheduledRunHistory(tmp_path).recent().runs[0]

    assert run.status == "failed"
    assert run.error == "APIConnectionError: Connection error."
    assert run.has_report is False


def test_history_reports_a_truncated_listing_rather_than_hiding_it(
    tmp_path: Path,
) -> None:
    """A limit must never quietly drop runs the reader asked to see."""
    for index in range(5):
        write_record(
            tmp_path, "news", f"2026-08-2{index}T07:00:00Z", f"eeeeeeee-{index}"
        )

    listing = ScheduledRunHistory(tmp_path).recent(limit=2)

    assert len(listing.runs) == 2
    assert listing.total == 5
    assert listing.truncated is True


def test_history_narrows_to_one_job(tmp_path: Path) -> None:
    """`/runs <job>` answers "how has this one been doing"."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "ffffffff-5555")
    write_record(tmp_path, "podcasts", "2026-08-31T03:00:00Z", "99999999-6666")

    listing = ScheduledRunHistory(tmp_path).recent(job_id="podcasts")

    assert [run.job_id for run in listing.runs] == ["podcasts"]
    assert listing.total == 1


def test_history_gives_each_run_a_short_handle(tmp_path: Path) -> None:
    """The handle is the identity, not the row number.

    A row number means something different in every listing, which is the
    "hope it shows the one I meant" problem this replaces.
    """
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "b20ba821-7bc4-42df")

    run = ScheduledRunHistory(tmp_path).recent().runs[0]

    assert run.short_id == "b20ba821"
    assert run.run_id.startswith(run.short_id)


def test_history_measures_how_long_a_run_took(tmp_path: Path) -> None:
    """Duration is the cheapest signal that a run did less than it should."""
    write_record(
        tmp_path,
        "news",
        "2026-08-30T07:00:00Z",
        "aaaaaaaa-7777",
        finished="2026-08-30T07:00:16Z",
    )

    run = ScheduledRunHistory(tmp_path).recent().runs[0]

    assert run.duration_seconds == pytest.approx(16.0)


def test_history_shows_a_run_it_cannot_read_rather_than_dropping_it(
    tmp_path: Path,
) -> None:
    """An unreadable record is still evidence that something ran."""
    (tmp_path / "news").mkdir(parents=True)
    (tmp_path / "news" / "20260830T070000Z-broken.json").write_text(
        "{not json", encoding="utf-8"
    )

    listing = ScheduledRunHistory(tmp_path).recent()

    assert listing.total == 1
    assert listing.runs[0].status == "unreadable"
    assert listing.runs[0].job_id == "news"


def test_history_of_a_directory_that_does_not_exist_is_empty(tmp_path: Path) -> None:
    """Nothing has run yet is an ordinary state, not a failure."""
    listing = ScheduledRunHistory(tmp_path / "absent").recent()

    assert listing.runs == ()
    assert listing.total == 0


def test_history_orders_a_run_with_no_start_time_last(tmp_path: Path) -> None:
    """Sorting must not raise on a record written by an older version."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "aaaaaaaa-8888")
    directory = tmp_path / "news"
    (directory / "20260101T000000Z-undated.json").write_text(
        json.dumps({"job_id": "news", "run_id": "undated", "status": "succeeded"}),
        encoding="utf-8",
    )

    listing = ScheduledRunHistory(tmp_path).recent()

    assert listing.total == 2
    assert listing.runs[-1].run_id == "undated"
    assert listing.runs[-1].started_at is None


def test_history_reads_the_records_this_project_actually_wrote() -> None:
    """The parser is checked against a real record, not only fixtures."""
    record = {
        "job_id": "weekday-ai-news",
        "run_id": "b20ba821-7bc4-42df-b2fe-fca9c81dfad0",
        "started_at": "2026-08-31T17:42:05.683089Z",
        "status": "succeeded",
        "finished_at": "2026-08-31T17:42:21.801333Z",
        "report_path": "weekday-ai-news/20260831T174205Z-b20ba821.md",
        "error": None,
        "task": "web_research",
        "prompt": "Research the most important AI-agent developments from yesterday.",
        "search_category": "news",
        "start_date": "2026-08-30",
        "end_date": "2026-08-31",
    }
    run = ScheduledRunHistory.read_record(record, job_id="weekday-ai-news")

    assert run.short_id == "b20ba821"
    assert run.status == "succeeded"
    assert run.started_at == datetime(2026, 8, 31, 17, 42, 5, 683089, tzinfo=UTC)
    assert run.duration_seconds == pytest.approx(16.118244)


def test_history_sorts_two_runs_that_both_lack_a_start_time(tmp_path: Path) -> None:
    """Two undated records must not make the sort compare None with None."""
    directory = tmp_path / "news"
    directory.mkdir(parents=True)
    for name in ("one", "two"):
        (directory / f"20260101T000000Z-{name}.json").write_text(
            json.dumps({"job_id": "news", "run_id": name, "status": "succeeded"}),
            encoding="utf-8",
        )

    listing = ScheduledRunHistory(tmp_path).recent()

    assert listing.total == 2
    assert {run.run_id for run in listing.runs} == {"one", "two"}


def test_history_says_which_job_a_listing_was_narrowed_to(tmp_path: Path) -> None:
    """An empty result for one job must not read as "nothing has ever run".

    Nine runs existed and `/runs nope` still said "no scheduled runs recorded
    yet", which is the kind of quiet lie this listing exists to stop telling.
    """
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "aaaaaaaa-9999")

    listing = ScheduledRunHistory(tmp_path).recent(job_id="absent-job")

    assert listing.runs == ()
    assert listing.total == 0
    assert listing.job_id == "absent-job"


def test_history_finds_a_run_by_its_short_handle(tmp_path: Path) -> None:
    """The handle shown in the listing is the handle that opens the run."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "b20ba821-7bc4-42df")

    found = ScheduledRunHistory(tmp_path).find("b20ba821")

    assert [run.run_id for run in found] == ["b20ba821-7bc4-42df"]


def test_history_finds_a_run_by_its_full_id(tmp_path: Path) -> None:
    """Pasting the whole ID from a record must work too."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "b20ba821-7bc4-42df")

    found = ScheduledRunHistory(tmp_path).find("b20ba821-7bc4-42df")

    assert len(found) == 1


def test_history_returns_every_match_rather_than_guessing(tmp_path: Path) -> None:
    """An ambiguous handle must not silently open one of the candidates.

    Opening "probably the one you meant" is the behaviour this whole feature
    replaced. Two matches is a question for the reader, not a coin toss.
    """
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "aaaa1111-one")
    write_record(tmp_path, "news", "2026-08-29T07:00:00Z", "aaaa2222-two")

    found = ScheduledRunHistory(tmp_path).find("aaaa")

    assert len(found) == 2


def test_history_finds_nothing_for_an_unknown_handle(tmp_path: Path) -> None:
    """No match is an answer, not an error."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "b20ba821-7bc4")

    assert ScheduledRunHistory(tmp_path).find("zzzzzzzz") == ()


def test_history_reads_the_report_a_run_produced(tmp_path: Path) -> None:
    """Reading a run means reading the report it wrote."""
    write_record(tmp_path, "news", "2026-08-30T07:00:00Z", "b20ba821-7bc4")
    history = ScheduledRunHistory(tmp_path)
    run = history.find("b20ba821")[0]

    assert history.read_report(run) == "# report\n"


def test_history_has_no_report_to_read_for_a_failed_run(tmp_path: Path) -> None:
    """A failed run deletes its report; that is not an error to raise."""
    write_record(
        tmp_path,
        "news",
        "2026-08-18T11:00:00Z",
        "dddddddd-4444",
        status="failed",
        error="APIConnectionError: Connection error.",
        with_report=False,
    )
    history = ScheduledRunHistory(tmp_path)

    assert history.read_report(history.find("dddddddd")[0]) is None


def test_history_refuses_a_report_path_that_escapes_the_root(tmp_path: Path) -> None:
    """`report_path` comes out of a file, so it is input, not a fact.

    A record is JSON on disk. Following whatever path it names would turn any
    writable record into a file-read primitive.
    """
    secret = tmp_path / "secret.md"
    secret.write_text("do not read me", encoding="utf-8")
    root = tmp_path / "runs"
    (root / "news").mkdir(parents=True)
    (root / "news" / "20260830T070000Z-evil.json").write_text(
        json.dumps(
            {
                "job_id": "news",
                "run_id": "evil",
                "status": "succeeded",
                "started_at": "2026-08-30T07:00:00Z",
                "report_path": "../../secret.md",
            }
        ),
        encoding="utf-8",
    )
    history = ScheduledRunHistory(root)

    assert history.read_report(history.find("evil")[0]) is None
