"""The command vocabulary and its reading, shared by every front end.

Two interfaces accept the same typed commands. Keeping the table, the parse,
and the one reference both of them print here means a new specialist appears in
both by editing one place, and neither interface can quietly drift into
supporting a command the other does not.

What each interface then *does* about a command is its own: the command line
prints, the terminal interface writes to a log and opens panes. This module
decides what was asked for and stops there. The single exception is the command
reference itself and the scheduled-run listing, which both render identically
from the same tables — building the reference twice was how the reason for
using `Text` ended up recorded in only one of them, and a listing whose
truncation notice appeared in one interface and not the other would be the
same mistake with worse consequences.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from oris.scheduled_run_history import (
    SHORT_ID_LENGTH,
    ScheduledRunHistory,
    ScheduledRunListing,
)
from oris.schedules import (
    DEFAULT_SCHEDULE_FILE,
    load_schedule_config,
    next_run_time,
)

SLASH_COMMANDS = {
    "/research": (
        "web_research",
        "<question>",
        "Search the open web with Tavily.",
    ),
    "/community": (
        "community_research",
        "[x|hn|arxiv|all] <topic>",
        "A week of X, Hacker News and arXiv; name sources to narrow it.",
    ),
    "/recall": (
        "local_knowledge",
        "<question>",
        "Search your archive of past chats and reports.",
    ),
    "/podcasts": (
        "podcast_catch_up",
        "[list|summarize_prev] [show]",
        "New episodes; 'list' names your shows, "
        "'summarize_prev' re-reads ones already caught up on.",
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
    (
        "/runs [job|id]",
        "List scheduled runs, or print one by its ID. Not sent to chat.",
    ),
    (
        "/schedule [run <job>]",
        "Show the configured jobs and next run times; 'run' starts one now.",
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

SelfHandledName = Literal[
    "exit",
    "help",
    "session",
    "new",
    "show_evidence",
    "show_runs",
    "show_schedule",
    "run_job",
]


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

    if query == "/runs" or query.startswith("/runs "):
        return SelfHandled("show_runs", query.removeprefix("/runs").strip())

    if query == "/schedule":
        return SelfHandled("show_schedule")

    if query.startswith("/schedule run"):
        job_id = query.removeprefix("/schedule run").strip()
        if not job_id:
            return Rejected("Usage: /schedule run <job>")
        return SelfHandled("run_job", job_id)

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


STATUS_STYLES = {
    "succeeded": "green",
    "failed": "red",
    "running": "yellow",
    "unreadable": "red",
}


def _when(run) -> str:
    return "unknown" if run.started_at is None else f"{run.started_at:%b %d %H:%M}"


def _took(run) -> str:
    seconds = run.duration_seconds
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f}m"


MAX_ERROR_CHARS = 96
"""How much of a failure reason a listing row shows.

Enough for the sentence that names the fault, short enough that one stack
trace cannot push every other column off the screen. The full text is in the
run record.
"""


def run_table(listing: ScheduledRunListing) -> RenderableType:
    """Render one scheduled-run listing for either interface.

    Everything here guards the same thing: that the reader cannot be misled
    about what the list left out.

    Failures are rows like any other, with the reason on the row. A failed run
    deletes its report, so a listing that only showed runs worth opening would
    hide failures entirely -- which is how two of them went unnoticed in this
    project for three weeks.

    A truncated listing says so and gives the total. An empty one says whether
    it is empty because nothing has run or because nothing matched the job it
    was narrowed to; those are different facts and rendering them alike told
    the reader there was no history when there were nine runs.
    """
    if not listing.runs:
        message = (
            f"No scheduled runs recorded for {listing.job_id!r}."
            if listing.job_id
            else "No scheduled runs recorded yet."
        )
        return Text(message, style="dim")

    table = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Job", no_wrap=True)
    table.add_column("Started", style="dim", no_wrap=True)
    table.add_column("Took", style="dim", justify="right", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    for run in listing.runs:
        status = Text(run.status, style=STATUS_STYLES.get(run.status, ""))
        if run.error:
            # On the same row rather than beneath it. A second row put the
            # reason in the Job column and stretched it to the width of the
            # longest error in the listing.
            reason = run.error
            if len(reason) > MAX_ERROR_CHARS:
                reason = reason[: MAX_ERROR_CHARS - 1] + "\u2026"
            status.append(f" — {reason}", style="red")
        table.add_row(
            Text(run.short_id),
            Text(run.job_id),
            Text(_when(run)),
            Text(_took(run)),
            status,
        )

    scope = f" for {listing.job_id}" if listing.job_id else ""
    plural = "" if listing.total == 1 else "s"
    note = (
        f"Showing {len(listing.runs)} of {listing.total} runs{scope}."
        if listing.truncated
        else f"{listing.total} run{plural}{scope}."
    )
    return Group(table, Text(note, style="dim"))


def render_runs(history: ScheduledRunHistory, argument: str) -> RenderableType:
    """Answer one `/runs` command, whatever it was asking for.

    The argument is either a job or a run handle, and both are lowercase words
    with hyphens, so shape cannot tell them apart. A job wins when a directory
    of that name exists, because a job only exists once it has recorded a run.
    A handle that happens to equal a job name is the rarer accident and loses.

    An ambiguous handle prints the candidates instead of opening one. Guessing
    which run the reader meant is the behaviour this replaced.
    """
    wanted = argument.strip()
    if not wanted:
        return run_table(history.recent())
    if wanted in history.job_ids():
        return run_table(history.recent(job_id=wanted))

    matches = history.find(wanted)
    if not matches:
        return Text(
            f"No scheduled run or job matches {wanted!r}.",
            style="yellow",
        )
    if len(matches) > 1:
        listing = ScheduledRunListing(runs=matches, total=len(matches), truncated=False)
        return Group(
            Text(f"{wanted!r} matches more than one run:", style="yellow"),
            run_table(listing),
        )

    run = matches[0]
    report = history.read_report(run)
    if report is None:
        reason = run.error or "It recorded no report."
        return Text(
            f"Run {run.short_id} ({run.job_id}, {run.status}) has nothing to "
            f"show. {reason}",
            style="yellow",
        )
    header = Text(
        f"{run.short_id}  {run.job_id}  {_when(run)}  {run.status}",
        style="dim",
    )
    return Group(header, Text(""), Text(report))


def render_schedule(
    path: Path = DEFAULT_SCHEDULE_FILE,
    *,
    now: datetime | None = None,
) -> RenderableType:
    """Show the configured jobs and when each next fires.

    Times are in the schedule's own timezone, because a schedule written for
    Detroit answered in UTC is a schedule read wrong.

    A disabled job gets no next-run time. Computing one would be accurate about
    the cron and wrong about what will happen.

    A cron this cannot read is named on its own row rather than taking the
    command down. `ScheduleConfig` only requires that `cron` is a non-empty
    string, so a malformed expression loads happily and fails much later when
    the scheduler starts -- which is exactly when a person wants to have been
    told.
    """
    try:
        config = load_schedule_config(path)
    except FileNotFoundError:
        return Text(f"No schedule file at {path}.", style="yellow")
    except (OSError, ValueError) as error:
        return Text(f"Could not read {path}: {error}", style="yellow")

    if not config.jobs:
        return Text(f"{path} configures no jobs.", style="dim")

    after = now or datetime.now(ZoneInfo(config.timezone))
    table = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    table.add_column("Job", style="bold cyan", no_wrap=True)
    table.add_column("Task", no_wrap=True)
    table.add_column("Cron", style="dim", no_wrap=True)
    table.add_column("Next run", no_wrap=True)
    for job in config.jobs:
        if not job.enabled:
            when = Text("disabled", style="dim")
        else:
            try:
                fires = next_run_time(job.cron, config.timezone, after=after)
            except ValueError as error:
                when = Text(f"invalid cron: {error}", style="red")
            else:
                when = Text(f"{fires:%b %d %H:%M}", style="green")
        table.add_row(Text(job.id), Text(job.task), Text(job.cron), when)

    return Group(table, Text(f"Times are {config.timezone}.", style="dim"))


def run_job_now(
    job_id: str,
    *,
    path: Path = DEFAULT_SCHEDULE_FILE,
) -> RenderableType:
    """Run one scheduled job on demand and say where its report landed.

    A job takes minutes and calls live providers, so both interfaces have to
    keep it off whatever thread is drawing the screen. What they share is this:
    what counts as a bad job name, and what a person is told afterwards.

    A failure is reported rather than raised. Asking for a run by hand is
    usually how someone checks whether a job works at all, and losing the
    interface is the least useful possible answer to "it does not".
    """
    from oris.scheduled_runs import UnknownScheduledJob, run_job_by_id

    try:
        record = run_job_by_id(job_id, schedule_file=path)
    except UnknownScheduledJob as error:
        return Text(str(error), style="yellow")
    except Exception as error:  # noqa: BLE001 - a failed job is not a crash
        return Text(f"{job_id} failed: {type(error).__name__}: {error}", style="red")

    if record.status != "succeeded":
        return Text(
            f"{job_id} finished as {record.status}: {record.error or 'no reason given'}",
            style="red",
        )
    return Group(
        Text(f"{job_id} succeeded.", style="green"),
        Text(f"Run {str(record.run_id)[:SHORT_ID_LENGTH]}", style="dim"),
        Text(f"Report: {record.report_path}", style="dim"),
    )


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
