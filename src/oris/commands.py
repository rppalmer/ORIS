"""The command vocabulary and its reading, shared by every front end.

Two interfaces accept the same typed commands. Keeping the table, the parse,
and the one reference both of them print here means a new specialist appears in
both by editing one place, and neither interface can quietly drift into
supporting a command the other does not.

What each interface then *does* about a command is its own: the command line
prints, the terminal interface writes to a log and opens panes. This module
decides what was asked for and stops there. The single exception is the command
reference itself, which both render identically from the same table — building
it twice was how the reason for using `Text` ended up recorded in only one of
them.
"""

from dataclasses import dataclass
from typing import Literal

from rich.table import Table
from rich.text import Text

SLASH_COMMANDS = {
    "/research": (
        "web_research",
        "<question>",
        "Search the open web with Tavily.",
    ),
    "/community": (
        "community_research",
        "<topic>",
        "One day of X and Hacker News, a week of arXiv, 10 results each.",
    ),
    "/recall": (
        "local_knowledge",
        "<question>",
        "Search your archive of past chats and reports.",
    ),
    "/podcasts": (
        "podcast_catch_up",
        "[list|recap] [show]",
        "Catch up on new episodes; 'list' names your shows, 'recap' re-reads.",
    ),
    "/threat": (
        "threat_intel",
        "[report] [enrich|ref] <target>",
        "Defensive ThreatSyft lookup; 'enrich' egresses indicators to providers.",
    ),
}

OPTIONAL_ARGUMENT_COMMANDS = frozenset({"/podcasts"})
"""Commands that mean something on their own.

Every other slash command needs a subject: `/research` without a question has
nothing to research. Podcast Catch-up already knows what to catch up on, because
the feeds are configured in Net-Razor, so an empty line is the ordinary way to
use it and naming a show is the narrowing.
"""


# Commands an interface answers itself, without reaching the graph.
SIMPLE_COMMANDS = (
    (
        "/threat show [id] [source]",
        "Print stored evidence, newest by default. Not sent to chat.",
    ),
    ("/session", "Show the active session ID."),
    ("/new", "Start a new conversation session."),
    ("/help", "Show these commands."),
    ("/exit", "Quit."),
)

# `/quit` answers to the same thing and is not advertised separately. It exists
# because one interface accepted it and the other did not, which is the drift
# this module was created to stop.
EXIT_COMMANDS = ("/exit", "/quit")

HELP_FLAGS = frozenset({"--help", "-h"})
"""How a command asks for its own reference instead of running.

Only the flag spellings, never a bare `help`: `/research help` is a perfectly
good question about help, and a command that quietly refused to search for its
own argument would be worse than one that never offered help at all.
"""

WORKING_LABELS = {
    "web_research": "Web Research",
    "podcast_catch_up": "Podcast Catch-up",
    "community_research": "Community Research",
    "local_knowledge": "Local Knowledge",
    "threat_intel": "Threat Intel",
}
DEFAULT_WORKING_LABEL = "Thinking"

# Node names mostly read well once the underscores are gone, so only the ones
# that would read oddly to someone who has not seen the graph are named here. A
# node with no entry still gets a sensible label, which means adding one to a
# specialist costs nothing and never shows a blank step.
PHASE_LABELS = {
    "discover_episodes": "checking your feeds",
    "obtain_transcripts": "fetching and transcribing",
    "summarize_episodes": "summarising each episode",
    "plan_investigation": "choosing what to look up",
    "extract_indicators": "reading the request",
    "collect_evidence": "querying providers",
    "compile_report": "building the report",
    "synthesize_answer": "writing the answer",
    "validate_sources": "checking citations",
    "validate_answer": "checking citations",
    "plan_search": "planning the search",
    "search_web": "searching the web",
    "retrieve_knowledge": "searching the archive",
    "answer_from_knowledge": "reading the archive",
    "mark_processed": "acknowledging episodes",
}

SelfHandledName = Literal["exit", "help", "session", "new", "show_evidence"]


@dataclass(frozen=True)
class Routed:
    """A request bound for the graph, with the specialist already chosen."""

    mode: str
    request: str


@dataclass(frozen=True)
class SelfHandled:
    """A command the interface answers itself, without reaching the graph."""

    name: SelfHandledName
    argument: str = ""


@dataclass(frozen=True)
class Rejected:
    """Nothing runs; the interface shows this line and returns to the prompt."""

    message: str


def help_topic(name: str) -> str | None:
    """The command whose reference `name` asks for, or None if there is none.

    Accepts the name with or without its leading slash, because `/help podcasts`
    is what people type at least as often as `/help /podcasts`.
    """
    command = name if name.startswith("/") else f"/{name}"
    if command in EXIT_COMMANDS:
        return "/exit"
    if command in SLASH_COMMANDS:
        return command
    if any(entry.split()[0] == command for entry, _description in SIMPLE_COMMANDS):
        return command
    return None


def read_command(query: str) -> Routed | SelfHandled | Rejected:
    """Decide what one line of input asks for.

    Every interface has to make the same five distinctions — quit, show the
    reference, name the session, start a new one, print stored evidence — and
    then the same two: route a known slash command, or refuse an unknown one.
    Written out per interface those agreed until one of them changed.
    """
    if query in EXIT_COMMANDS:
        return SelfHandled("exit")
    if query == "/help":
        return SelfHandled("help")
    if query == "/session":
        return SelfHandled("session")
    if query == "/new":
        return SelfHandled("new")

    # Either spelling of "explain this one command": `/help /podcasts`, or the
    # flag on the command itself. The leading slash is required so that an
    # ordinary two-word message ending in `-h` is still a message.
    words = query.split()
    if (
        len(words) == 2
        and words[0].startswith("/")
        and (words[0] == "/help" or words[1] in HELP_FLAGS)
    ):
        wanted = words[1] if words[0] == "/help" else words[0]
        topic = help_topic(wanted)
        if topic is None:
            return Rejected(f"Unknown command: {wanted}")
        return SelfHandled("help", topic)

    if query.startswith("/threat show"):
        return SelfHandled("show_evidence", query.removeprefix("/threat show").strip())

    command = query.split(maxsplit=1)[0]
    if command in SLASH_COMMANDS:
        mode, argument, _description = SLASH_COMMANDS[command]
        request = query.removeprefix(command).strip()
        if not request and command not in OPTIONAL_ARGUMENT_COMMANDS:
            return Rejected(f"Usage: {command} {argument}")
        return Routed(mode, request)
    if command.startswith("/"):
        return Rejected(f"Unknown command: {command}")
    return Routed("auto", query)


def command_table(command: str = "") -> Table:
    """Build the command reference both interfaces show at startup and on `/help`.

    Naming a command narrows the table to that command's own lines. `/threat`
    has two of them, one for the lookup and one for `/threat show`, and both
    belong in its help — which is why the filter matches on the first word
    rather than on the whole usage string.

    `Text`, not `str`: usage strings are full of square brackets for optional
    arguments, and rich would read "[report]" and "[source]" as markup tags and
    silently delete them, leaving the reference lying about the syntax.
    """
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="dim")
    for name, (_mode, argument, description) in SLASH_COMMANDS.items():
        if command in ("", name):
            table.add_row(Text(f"{name} {argument}"), Text(description))
    for usage, description in SIMPLE_COMMANDS:
        if command in ("", usage.split()[0]):
            table.add_row(Text(usage), Text(description))
    return table


def working_label(mode: str) -> str:
    """Name the specialist a request is headed for, for a progress indicator."""
    return WORKING_LABELS.get(mode, DEFAULT_WORKING_LABEL)


def phase_label(node: str) -> str:
    """Say what a running graph node is doing, in the reader's terms.

    A `/threat` run takes half a minute and spends most of it inside one model
    call, so a single unchanging label for the whole wait cannot distinguish
    working from hung. Naming the step turns the wait into progress.
    """
    return PHASE_LABELS.get(node, node.replace("_", " "))
