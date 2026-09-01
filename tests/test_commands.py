"""Tests for the command vocabulary both front ends read."""

from datetime import UTC, datetime

from rich.console import Console

from oris.commands import (
    Rejected,
    Routed,
    SelfHandled,
    command_table,
    read_command,
    run_table,
    working_label,
)
from oris.scheduled_run_history import ScheduledRun, ScheduledRunListing


def test_command_help_shows_bracketed_usage_verbatim(capsys) -> None:
    """Square brackets mark optional arguments; console markup eats them.

    Rendered as markup, "[report]" and "[source]" are parsed as unknown tags and
    dropped, leaving the reference describing a syntax that does not exist. Both
    interfaces print this table, so the rule has to hold in the one place it is
    now built rather than in whichever copy someone remembered.
    """
    Console(width=200).print(command_table())

    printed = capsys.readouterr().out
    assert "[report] [enrich|ref] <target>" in printed
    assert "/threat show [id] [source]" in printed


def test_a_slash_command_carries_its_specialist_and_its_request() -> None:
    """Routing is decided before any interface sees the line."""
    assert read_command("/research what is LangGraph") == Routed(
        "web_research", "what is LangGraph"
    )


def test_ordinary_text_is_routed_to_the_router() -> None:
    """Anything not addressed to a specialist is the router's decision."""
    assert read_command("what did we decide") == Routed("auto", "what did we decide")


def test_a_slash_command_without_its_argument_shows_its_usage() -> None:
    """A bare command is a typo, not a request to search for nothing."""
    result = read_command("/research")

    assert isinstance(result, Rejected)
    assert result.message == "Usage: /research <question>"


def test_an_unknown_slash_command_is_refused_rather_than_routed() -> None:
    """Otherwise a mistyped command is silently sent to the model as a question.

    The model would then answer it, spending a turn and archiving a reply to
    something the user never asked.
    """
    result = read_command("/reserach LangGraph")

    assert isinstance(result, Rejected)
    assert result.message == "Unknown command: /reserach"


def test_the_interface_handled_commands_are_named_not_matched_twice() -> None:
    """Both front ends make these same five distinctions.

    Written out separately they agreed until one changed: the terminal
    interface accepted `/quit` and the command line did not, which is exactly
    the drift a shared vocabulary exists to prevent.
    """
    assert read_command("/help") == SelfHandled("help")
    assert read_command("/session") == SelfHandled("session")
    assert read_command("/new") == SelfHandled("new")
    assert read_command("/exit") == SelfHandled("exit")
    assert read_command("/quit") == SelfHandled("exit")


def test_showing_evidence_keeps_its_arguments() -> None:
    """`/threat show` takes an optional report ID and an optional source."""
    assert read_command("/threat show a3f21c shodan") == SelfHandled(
        "show_evidence", "a3f21c shodan"
    )
    assert read_command("/threat show") == SelfHandled("show_evidence", "")


def test_a_threat_request_is_not_mistaken_for_the_evidence_viewer() -> None:
    """`/threat show` is a prefix of nothing else, but `/threat` is a prefix of it.

    Getting this backwards would send a lookup to the local viewer, or send a
    viewer request to the providers and spend credits on it.
    """
    assert read_command("/threat enrich 8.8.8.8") == Routed(
        "threat_intel", "enrich 8.8.8.8"
    )


def test_the_progress_label_names_the_specialist() -> None:
    """A spinner that says what is running is the only progress signal there is."""
    assert working_label("web_research") == "Web Research"
    assert working_label("auto") == "Thinking"


def test_a_command_that_needs_no_argument_runs_without_one() -> None:
    """`/podcasts` takes its scope from the configured feeds, not from the line.

    Every other slash command needs something to act on, so the parser refused
    an empty one. This command's whole point is that it already knows what to
    catch up on.
    """
    assert read_command("/podcasts") == Routed("podcast_catch_up", "")


def test_a_command_that_needs_an_argument_still_refuses_an_empty_one() -> None:
    """The relaxation is per command, not a general loosening."""
    assert read_command("/research") == Rejected("Usage: /research <question>")


def test_a_named_show_reaches_the_specialist_as_the_request() -> None:
    """`/podcasts <show>` narrows the run to one configured show."""
    assert read_command("/podcasts linux unplugged") == Routed(
        "podcast_catch_up", "linux unplugged"
    )


def test_every_command_answers_for_its_own_usage() -> None:
    """`--help` on a command is help, never that command's argument.

    `/podcasts --help` used to be read as the name of a show to catch up on,
    which searched the feeds for a podcast called "--help" and reported that
    nobody follows it. Any command can be asked, including the ones an
    interface answers itself.
    """
    assert read_command("/podcasts --help") == SelfHandled("help", "/podcasts")
    assert read_command("/research -h") == SelfHandled("help", "/research")
    assert read_command("/new --help") == SelfHandled("help", "/new")
    assert read_command("/quit --help") == SelfHandled("help", "/exit")


def test_help_also_takes_the_command_as_its_argument() -> None:
    """`/help /podcasts` and `/help podcasts` ask the same question."""
    assert read_command("/help /podcasts") == SelfHandled("help", "/podcasts")
    assert read_command("/help podcasts") == SelfHandled("help", "/podcasts")
    assert read_command("/help nonsense") == Rejected("Unknown command: nonsense")


def test_a_two_word_question_is_not_a_help_request() -> None:
    """The flag only means help on something already spelled as a command.

    Without the leading-slash requirement, an ordinary message that happened to
    end in `-h` would stop being a message.
    """
    assert read_command("bandwidth -h") == Routed("auto", "bandwidth -h")


def test_help_for_one_command_shows_that_command_alone(capsys) -> None:
    """Narrowed help keeps every line the command owns and drops the rest.

    `/threat` has two lines, the lookup and the evidence viewer, and someone
    asking about `/threat` needs both.
    """
    Console(width=200).print(command_table("/threat"))
    printed = capsys.readouterr().out

    assert "[report] [enrich|ref] <target>" in printed
    assert "/threat show [id] [source]" in printed
    assert "/podcasts" not in printed


def test_runs_is_answered_by_the_interface() -> None:
    """Listing what ran reads files; it must not cost a model call."""
    assert read_command("/runs") == SelfHandled("show_runs", "")


def test_runs_narrows_to_one_job() -> None:
    """`/runs <job>` answers "how has this one been doing"."""
    assert read_command("/runs weekday-ai-news") == SelfHandled(
        "show_runs", "weekday-ai-news"
    )


def test_runs_appears_in_its_own_help() -> None:
    """A command absent from the reference is a command nobody finds."""
    console = Console(width=100)
    with console.capture() as captured:
        console.print(command_table("/runs"))

    assert "/runs" in captured.get()


def _rendered(listing) -> str:
    console = Console(width=110)
    with console.capture() as captured:
        console.print(run_table(listing))
    return captured.get()


def test_run_table_shows_a_failure_and_its_reason() -> None:
    """A failed run has no report, so the row is the only place to see it."""
    listing = ScheduledRunListing(
        runs=(
            ScheduledRun(
                run_id="818183b8-7c5e-46bd",
                job_id="weekday-ai-news",
                status="failed",
                started_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
                finished_at=datetime(2026, 8, 18, 11, 0, 2, tzinfo=UTC),
                error="APIConnectionError: Connection error.",
                report_path=None,
                task="web_research",
            ),
        ),
        total=1,
        truncated=False,
    )

    output = _rendered(listing)

    assert "818183b8" in output
    assert "failed" in output
    assert "APIConnectionError" in output


def test_run_table_says_when_it_did_not_show_everything() -> None:
    """Silent truncation is the failure this listing exists to avoid."""
    run = ScheduledRun(
        run_id="b20ba821-7bc4",
        job_id="weekday-ai-news",
        status="succeeded",
        started_at=datetime(2026, 8, 31, 17, 42, tzinfo=UTC),
        finished_at=datetime(2026, 8, 31, 17, 42, 16, tzinfo=UTC),
        error=None,
        report_path="weekday-ai-news/x.md",
        task="web_research",
    )

    output = _rendered(ScheduledRunListing(runs=(run,), total=9, truncated=True))

    assert "9" in output
    assert "16s" in output


def test_run_table_says_plainly_when_nothing_has_run() -> None:
    """An empty history is an ordinary state and must not render as a blank."""
    output = _rendered(ScheduledRunListing(runs=(), total=0, truncated=False))

    assert "No scheduled runs" in output


def test_run_table_names_the_job_when_that_job_has_no_runs() -> None:
    """Nine runs existing and this one having none are different facts."""
    output = _rendered(
        ScheduledRunListing(runs=(), total=0, truncated=False, job_id="absent-job")
    )

    assert "absent-job" in output


def test_run_table_keeps_a_long_error_from_stretching_the_columns() -> None:
    """A stack trace in one row must not push every other column off screen."""
    listing = ScheduledRunListing(
        runs=(
            ScheduledRun(
                run_id="818183b8-7c5e",
                job_id="news",
                status="failed",
                started_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
                finished_at=datetime(2026, 8, 18, 11, 0, 2, tzinfo=UTC),
                error="SearchProviderError: " + "x" * 400,
                report_path=None,
                task="web_research",
            ),
        ),
        total=1,
        truncated=False,
    )

    for line in _rendered(listing).splitlines():
        assert len(line) <= 110
