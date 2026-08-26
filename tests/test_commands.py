"""Tests for the command vocabulary both front ends read."""

from rich.console import Console

from oris.commands import (
    Rejected,
    Routed,
    SelfHandled,
    command_table,
    read_command,
    working_label,
)


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
