"""Command-line chat interface for ORIS."""

import asyncio
import json
import readline
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax
from rich.text import Text

from oris.chat import REQUEST_FAILURE_MESSAGE, run_turn
from oris.commands import (
    Rejected,
    SelfHandled,
    command_table,
    phase_label,
    read_command,
    render_runs,
    render_schedule,
    working_label,
)
from oris.knowledge import KnowledgeRepository
from oris.scheduled_run_history import DEFAULT_ROOT, ScheduledRunHistory
from oris.sessions import (
    ACTIVE_SESSION_FILENAME,
    load_or_create_session,
    start_new_session,
)
from oris.threat_reports import ThreatReportStore

CLI_HISTORY_FILENAME = "cli_history"
MAX_HISTORY_ENTRIES = 1000

BANNER = r"""
 ██████╗ ██████╗ ██╗███████╗
██╔═══██╗██╔══██╗██║██╔════╝
██║   ██║██████╔╝██║███████╗
██║   ██║██╔══██╗██║╚════██║
╚██████╔╝██║  ██║██║███████║
 ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝
"""
# Light at the top fading down, standing in for the dithered shading of the
# original artwork without depending on block-shading glyphs, which render
# inconsistently across terminal fonts.
BANNER_SHADES = ("grey93", "grey85", "grey74", "grey62", "grey50", "grey39")
BANNER_TAGLINE = "Orchestrator / Research / Analysis"
# Below this the art wraps and turns to noise, so the plain title is used.
MIN_BANNER_WIDTH = 36


@contextmanager
def cli_history(path: Path) -> Iterator[None]:
    """Persist input history across runs so the up arrow spans sessions.

    macOS ships libedit rather than GNU readline, which has no incremental
    `append_history_file`, so the whole list is written once on exit.
    """
    readline.set_history_length(MAX_HISTORY_ENTRIES)
    with suppress(OSError):
        readline.read_history_file(path)
    try:
        yield
    finally:
        path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            readline.write_history_file(path)


def print_banner(console: Console) -> None:
    """Print the startup banner, falling back when it would not render well.

    A piped or redirected session gets the plain title: the art is decoration,
    and a transcript or a test should not have to read around it.
    """
    if not console.is_terminal or console.width < MIN_BANNER_WIDTH:
        console.print(f"[bold]ORIS[/]  [dim]{BANNER_TAGLINE}[/]")
        return

    lines = BANNER.strip("\n").splitlines()
    for index, line in enumerate(lines):
        console.print(
            Text(line, style=BANNER_SHADES[min(index, len(BANNER_SHADES) - 1)])
        )
    console.print(Text(BANNER_TAGLINE, style="dim"))


def _working(console: Console) -> Progress:
    """Show a live spinner and elapsed time while a request runs."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=not console.is_terminal,
    )


def show_threat_report(
    console: Console,
    store: ThreatReportStore,
    arguments: str,
) -> None:
    """Print one stored evidence report, optionally narrowed to one source.

    Deliberately local: this never reaches the graph, so a report that is far
    too large for the model's context can be read in full without entering the
    conversation, the checkpoint database, or any later prompt.
    """
    report_id, _, wanted_source = arguments.partition(" ")
    report_id = report_id.strip()
    # No ID means the one just run, which is what asking for detail usually is.
    document = store.latest() if not report_id else store.load(report_id)
    if document is None:
        missing = f"No stored report {report_id!r}." if report_id else "No reports yet."
        console.print(
            Text(
                f"{missing} Reports are kept for {store.retention_days} days.",
                style="yellow",
            )
        )
        return

    evidence = document.get("evidence") or {}
    wanted_source = wanted_source.strip()
    if wanted_source:
        evidence = {
            key: _only_source(envelope, wanted_source)
            for key, envelope in evidence.items()
        }
        evidence = {key: value for key, value in evidence.items() if value}
        if not evidence:
            console.print(
                Text(f"No source {wanted_source!r} in that report.", style="yellow")
            )
            return

    console.print(
        Text(
            f"{document.get('request', '?')} · {document.get('created_at', '?')}",
            style="dim",
        )
    )
    rendered = json.dumps(evidence, indent=2, ensure_ascii=False)
    syntax = Syntax(rendered, "json", theme="ansi_dark", word_wrap=True)
    if console.is_terminal and rendered.count("\n") > console.size.height:
        with console.pager(styles=True):
            console.print(syntax)
    else:
        console.print(syntax)


def _only_source(envelope: object, wanted: str) -> object | None:
    """Narrow one evidence entry to a single provider, when it has one."""
    if not isinstance(envelope, dict):
        return None
    sources = (envelope.get("data") or {}).get("sources")
    if isinstance(sources, dict):
        match = {
            name: entry
            for name, entry in sources.items()
            if name.casefold() == wanted.casefold()
        }
        return {"data": {"sources": match}} if match else None
    return None


async def run_chat(
    graph: CompiledStateGraph,
    knowledge_repository: KnowledgeRepository,
    *,
    session_file_path: Path,
    thread_id: str,
    console: Console | None = None,
    threat_report_store: ThreatReportStore | None = None,
) -> None:
    """Read chat, research, or local archive requests and print responses."""
    # Runtime text — error messages, tool output, whatever the user typed — is
    # printed as `Text`, never interpolated into markup: a stray "[/]" in an
    # exception would otherwise raise MarkupError and kill the session, and a
    # stray "[bold]" would be silently swallowed.
    console = console or Console(highlight=False)
    print_banner(console)
    console.print()
    console.print(command_table())
    console.print(Text(f"Session: {thread_id}", style="dim"))

    while True:
        try:
            query = input("\nYou › ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not query:
            continue

        parsed = read_command(query)
        if isinstance(parsed, Rejected):
            console.print(Text(parsed.message, style="yellow"))
            continue
        if isinstance(parsed, SelfHandled):
            if parsed.name == "exit":
                return
            if parsed.name == "help":
                console.print(command_table(parsed.argument))
            elif parsed.name == "session":
                console.print(Text(f"Current session: {thread_id}", style="dim"))
            elif parsed.name == "new":
                thread_id = start_new_session(session_file_path)
                console.print(Text(f"Started new session: {thread_id}", style="dim"))
            elif parsed.name == "show_schedule":
                console.print(render_schedule())
            elif parsed.name == "show_runs":
                console.print(
                    render_runs(ScheduledRunHistory(DEFAULT_ROOT), parsed.argument)
                )
            # Named rather than left to `else`: a new self-handled command
            # added to the vocabulary would otherwise land here silently and
            # print stored evidence instead of doing its own job.
            elif parsed.name == "show_evidence":
                if threat_report_store is None:
                    console.print(
                        Text(
                            "Stored evidence reports are not configured.",
                            style="yellow",
                        )
                    )
                else:
                    show_threat_report(console, threat_report_store, parsed.argument)
            continue

        mode, query = parsed.mode, parsed.request
        label = working_label(mode)
        with _working(console) as progress:
            task = progress.add_task(label, total=None)

            def show(node: str, task_id: TaskID = task, name: str = label) -> None:
                progress.update(task_id, description=f"{name} · {phase_label(node)}")

            result = await run_turn(
                graph,
                {
                    "messages": [HumanMessage(content=query)],
                    "mode": mode,
                },
                {"configurable": {"thread_id": thread_id}},
                on_step=show,
            )

        if not result.get("request_succeeded", True):
            error_message = result.get("request_error") or REQUEST_FAILURE_MESSAGE
            console.print()
            console.print(Text(f"⚠ {error_message}", style="red"))
            continue

        response = result["messages"][-1]
        response_text = str(response.text)
        # Show the answer before archiving it: work that has already been done,
        # and external state that has already been acknowledged, must not be
        # lost because the local archive write failed.
        console.print("\n[bold green]ORIS[/]")
        console.print(Markdown(response_text))
        knowledge_repository.add_exchange(
            thread_id=thread_id,
            request=query,
            answer=response_text,
            selected_mode=result.get("selected_mode", mode),
        )


async def _main() -> None:
    """Build ORIS and run its asynchronous command-line interface."""
    from oris.web_research_app import (
        build_oris_graph,
        knowledge_repository,
        settings,
        threat_report_store,
    )

    database_path = settings.checkpoint_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    session_file_path = database_path.parent / ACTIVE_SESSION_FILENAME
    thread_id = load_or_create_session(session_file_path)

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        graph = await build_oris_graph(checkpointer)
        with cli_history(database_path.parent / CLI_HISTORY_FILENAME):
            await run_chat(
                graph,
                knowledge_repository,
                session_file_path=session_file_path,
                thread_id=thread_id,
                threat_report_store=threat_report_store,
            )


def main() -> None:
    """Start the asynchronous ORIS command-line application."""
    asyncio.run(_main())
