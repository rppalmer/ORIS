"""Terminal interface for ORIS: the conversation, and what it cost.

Two tabs, because there are two questions. Chat is the working surface. Activity
answers "what did that actually do" from the traces Phoenix already collected —
which node ran, how long it took, how many tokens it spent, which prompt the
model was given, and which stored evidence the answer came from.

Everything read here is read-only and every source is allowed to be absent.
Tracing is optional, Phoenix may never have run, and evidence reports age out;
none of that is a reason for the chat to stop working, so each pane degrades to
an explanatory line instead of an error.

The interface owns no behavior of its own. It parses the same commands as the
command line, calls the same graph, and archives to the same repository, so the
two front ends cannot answer the same request differently.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    ListItem,
    ListView,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
)

from oris.chat import REQUEST_FAILURE_MESSAGE, run_turn
from oris.commands import (
    Rejected,
    SelfHandled,
    command_table,
    phase_label,
    read_command,
    working_label,
)
from oris.knowledge import KnowledgeRepository
from oris.launch_agent import LaunchAgentPaths, is_loaded
from oris.launch_agent import restart as restart_service
from oris.launch_agent import start as start_service
from oris.launch_agent import stop as stop_service
from oris.observability import (
    Span,
    SystemPrompt,
    Trace,
    newest_trace_at,
    recent_traces,
    spans_for_trace,
    system_prompts_for_trace,
    trace_count,
)
from oris.sessions import (
    SessionSummary,
    delete_session,
    list_sessions,
    session_transcript,
    set_active_session,
    start_new_session,
)
from oris.threat_reports import TIMESTAMP_RESOLUTION_SECONDS, ThreatReportStore

TRACE_LIMIT = 50
REPORT_LIMIT = 200
# Everything that acts on the selected run. Evidence in particular has exactly
# one home: it belongs to a run, and runs are listed on one tab only.
ACTIVITY_ONLY_ACTIONS = frozenset(
    {
        "refresh_activity",
        "toggle_scope",
        "open_prompts",
        "open_evidence",
        "export",
        "phoenix_toggle",
        "phoenix_restart",
    }
)
# Sessions are listed on the chat tab, so that is where one can be deleted.
CHAT_ONLY_ACTIONS = frozenset({"delete_session"})
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
NO_TRACES = (
    "No traces recorded. Tracing is optional: start Phoenix with s, and set "
    "LOCAL_TRACING_ENABLED=true to record runs."
)
# The three states launchd can leave a service in, kept apart because the cure
# differs. A stopped service starts from here; one that was never installed
# cannot, and saying "stopped" would send someone looking for the wrong problem.
PHOENIX_RUNNING = "running"
PHOENIX_STOPPED = "stopped"
PHOENIX_NOT_INSTALLED = "not installed"


def phoenix_state(paths: LaunchAgentPaths | None) -> str:
    """Ask launchd what state the Phoenix service is in, without acting on it."""
    if paths is None:
        return PHOENIX_NOT_INSTALLED
    if not paths.installed.is_file():
        return PHOENIX_NOT_INSTALLED
    return PHOENIX_RUNNING if is_loaded(paths.label) else PHOENIX_STOPPED


# Said separately because the two look identical in an empty table and call for
# opposite responses. Naming the newest entry is what shows a collector that
# stopped days ago while the setting stayed on — the earlier wording told the
# reader to switch on something they had already switched on, which is easy to
# read as boilerplate and dismiss.
NO_TRACES_THIS_SESSION = (
    "No traces for this session. The newest recorded anywhere is {age}"
    " ({when}); press a to show every session."
)


def _age(moment: datetime) -> str:
    """Say how long ago something was, coarsely.

    "2 days ago" answers the question an absolute timestamp makes the reader do
    arithmetic for: is this stale, or did it just happen.
    """
    seconds = max(0.0, (datetime.now(UTC) - moment).total_seconds())
    for size, unit in ((86400.0, "day"), (3600.0, "hour"), (60.0, "minute")):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "moments ago"


def _count(number: int, noun: str) -> str:
    """Count something in readable English, because "1 turns" reads as a bug."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _local(moment: datetime) -> datetime:
    """Show times in the reader's timezone; everything is stored in UTC."""
    return moment.astimezone()


class _JsonScreen(ModalScreen):
    """Shared frame for the read-only viewers, which differ only in content."""

    BINDINGS = [Binding("escape,q", "dismiss", "Close")]
    CSS = """
    _JsonScreen { align: center middle; }
    #viewer { width: 90%; height: 90%; border: round $accent; padding: 0 1; }
    #viewer > Static { height: auto; }
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self.viewer_title = title

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="viewer")

    def on_mount(self) -> None:
        """Mount the whole viewer as one block of text, so it can be selected.

        A log cannot be copied out of. Both of these viewers exist to show
        something you then want somewhere else — evidence into a ticket, a
        prompt into an editor — so being unable to lift the text out defeats
        the point of having them.
        """
        heading = Text.assemble(
            (self.viewer_title, "bold"), ("  esc to close", "dim"), "\n\n"
        )
        self.query_one("#viewer", VerticalScroll).mount(
            Static(Text.assemble(heading, self.body()))
        )

    def body(self) -> Text:
        raise NotImplementedError


class EvidenceScreen(_JsonScreen):
    """The full provider evidence behind an answer, never sent to the model."""

    def __init__(self, document: dict[str, Any]) -> None:
        request = str(document.get("request", "?"))
        super().__init__(f"Evidence {document.get('report_id', '?')} · {request}")
        self.document = document

    def body(self) -> Text:
        """Highlighted JSON as `Text`, which is both coloured and selectable.

        `Syntax` renders to segments that keep no character positions, so a
        drag over one selects nothing. Asking it to highlight instead returns
        the same colours as a `Text`, which selection can read.
        """
        rendered = json.dumps(
            self.document.get("evidence") or {},
            indent=2,
            ensure_ascii=False,
        )
        return Syntax(rendered, "json", theme="ansi_dark", word_wrap=True).highlight(
            rendered
        )


class PromptScreen(_JsonScreen):
    """The system prompt each model call in one run was given, as it was sent."""

    def __init__(self, request: str, prompts: list[SystemPrompt]) -> None:
        super().__init__(f"Prompts · {request}")
        self.prompts = prompts

    def body(self) -> Text:
        if not self.prompts:
            return Text(
                "This run made no model calls, or its traces are not recorded.",
                style="yellow",
            )
        return Text("\n").join(
            Text.assemble(
                (prompt.span_name, "bold cyan"), "\n", (prompt.content, ""), "\n"
            )
            for prompt in self.prompts
        )


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Ask before a delete that cannot be undone, and say what it will take.

    A single keystroke on a highlighted list is far too cheap for an irreversible
    delete, and the archived answers go with the conversation, which is not
    obvious from the list.
    """

    BINDINGS = [
        Binding("escape,n", "cancel", "Cancel"),
        Binding("d,y", "confirm", "Delete"),
    ]
    CSS = """
    ConfirmDeleteScreen { align: center middle; }
    #confirm { width: 60; height: auto; border: round $error; padding: 1 2; }
    """

    def __init__(self, title: str, turns: int, archived: int, reports: int) -> None:
        super().__init__()
        self.session_title = title
        self.turns = turns
        self.archived = archived
        self.reports = reports

    def compose(self) -> ComposeResult:
        yield Static(
            Text.assemble(
                ("Delete this conversation?\n\n", "bold"),
                (f"  {self.session_title}\n", ""),
                (
                    f"  {self.turns} turns · {self.archived} archived answers · "
                    f"{self.reports} evidence reports\n\n",
                    "dim",
                ),
                ("  The conversation, its answers, and the full\n", ""),
                ("  provider evidence it collected are removed.\n", ""),
                ("  This cannot be undone.\n", ""),
                ("  Phoenix traces are not touched.\n\n", "dim"),
                ("  [d] delete    [esc] cancel", "dim"),
            ),
            id="confirm",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PromptInput(Input):
    """The chat entry box, with the up-arrow recall the command line has.

    History is the session's own requests rather than a separate file: what the
    user typed into this conversation is exactly what they expect to arrow back
    through, and it survives a restart because the conversation does.
    """

    BINDINGS = [
        Binding("up", "history(-1)", "Previous", show=False),
        Binding("down", "history(1)", "Next", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history: list[str] = []
        self._position = 0

    def remember(self, request: str) -> None:
        if request and (not self.history or self.history[-1] != request):
            self.history.append(request)
        self._position = len(self.history)

    def action_history(self, offset: int) -> None:
        if not self.history:
            return
        self._position = max(0, min(len(self.history), self._position + offset))
        # Stepping past the newest entry clears the box, which is how a shell
        # behaves and the only way back to an empty prompt without deleting.
        self.value = (
            "" if self._position == len(self.history) else self.history[self._position]
        )
        self.cursor_position = len(self.value)


class OrisTui(App):
    """Conversation on one tab, what it cost on the other."""

    TITLE = "ORIS"
    CSS = """
    #sessions { width: 34; border: round $panel; }
    /* Two lines per session, always. Wrapping a long request onto a third line
       loses the alignment that makes the list scannable, and the wrapped part
       carries no information the truncated title does not. */
    #sessions Static { text-wrap: nowrap; text-overflow: ellipsis; }
    #conversation { height: 1fr; border: round $panel; padding: 0 1; }
    /* Every message is a widget of its own, which is what makes the text
       selectable. A log would be simpler and cannot be selected: Textual reads
       the characters under a drag from content it rendered itself, and
       pre-rendered Rich output keeps no character positions to read. */
    #conversation > Static { height: auto; }
    #conversation > Markdown { height: auto; margin: 0 0 1 0; }
    /* The blank line that used to be written above and below each request. */
    #conversation > .ask { margin: 1 0; }
    /* Not docked, deliberately. Docking this to the same edge as the prompt
       put both in the same band, and the prompt — three rows tall against this
       one — was painted over the top of it. The status was set correctly the
       whole time and simply could not be seen. Left in normal flow it lands
       directly above the prompt, and an empty one is zero rows high, so the
       conversation gets the line back between turns. */
    #status { height: auto; padding: 0 1; }
    #prompt { dock: bottom; }
    /* The turn list carries six columns and the detail pane three, so an even
       split truncates the headings that matter at ordinary widths. */
    #turns { width: 3fr; height: 1fr; }
    #span-detail { width: 2fr; height: 1fr; border: round $panel; padding: 0 1; }
    #span-detail > Static, #viewer > Static { height: auto; }
    #summary { height: auto; padding: 0 1; color: $text-muted; }
    #services { height: auto; padding: 0 1; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("f1", "show_tab('chat')", "Chat"),
        Binding("f2", "show_tab('activity')", "Activity"),
        Binding("f5", "refresh_activity", "Refresh"),
        Binding("e", "open_evidence", "Evidence"),
        Binding("p", "open_prompts", "Prompts"),
        Binding("a", "toggle_scope", "Session/All"),
        Binding("x", "export", "Export"),
        Binding("d", "delete_session", "Delete session"),
        # Short labels on purpose: the footer already carries eight keys at
        # ordinary widths, and longer ones pushed this pair off the end of it.
        # What each does is spelled out on the status line beside the state.
        Binding("s", "phoenix_toggle", "Phoenix"),
        Binding("r", "phoenix_restart", "Restart"),
    ]

    def __init__(
        self,
        graph: CompiledStateGraph,
        knowledge_repository: KnowledgeRepository,
        *,
        thread_id: str,
        session_file_path: Path,
        checkpoint_database_path: Path,
        trace_database_path: Path,
        threat_report_store: ThreatReportStore | None = None,
        export_directory: Path | None = None,
        phoenix_url: str = "",
        phoenix_paths: LaunchAgentPaths | None = None,
    ) -> None:
        super().__init__()
        self.graph = graph
        self.knowledge_repository = knowledge_repository
        self.thread_id = thread_id
        self.session_file_path = session_file_path
        self.checkpoint_database_path = checkpoint_database_path
        self.trace_database_path = trace_database_path
        self.threat_report_store = threat_report_store
        self.export_directory = export_directory
        self.phoenix_url = phoenix_url
        self.phoenix_paths = phoenix_paths

        self._session_ids: list[str] = []
        self._summaries: dict[str, SessionSummary] = {}
        self._traces: list[Trace] = []
        self._spans: dict[str, list[Span]] = {}
        self._evidence_ids: dict[str, str] = {}
        self._every_session = False
        self._elapsed_timer: Any = None
        self._started_at = 0.0
        self._label = ""
        self._step = ""

    # -- layout --------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"), Horizontal():
                yield ListView(id="sessions")
                with Vertical():
                    yield VerticalScroll(id="conversation")
                    yield Static("", id="status")
                    yield PromptInput(placeholder="Ask, or /help …", id="prompt")
            with TabPane("Activity", id="activity"):
                yield Static("", id="summary")
                yield Static("", id="services")
                with Horizontal():
                    yield DataTable(id="turns", cursor_type="row")
                    yield VerticalScroll(id="span-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#turns", DataTable).add_columns(
            "Time", "Request", "Path", "Elapsed", "Tokens", "Status"
        )
        self._load_sessions()
        self._load_conversation()
        self.action_refresh_activity()
        self.query_one("#prompt", PromptInput).focus()

    # -- chat ----------------------------------------------------------------
    def _conversation(self) -> VerticalScroll:
        return self.query_one("#conversation", VerticalScroll)

    def _say(self, widget: Static | Markdown) -> None:
        """Add one message to the conversation and keep the newest in view.

        Each message is its own widget rather than a line written into a log.
        That is what makes the text selectable: Textual can only extract the
        characters under a drag from a widget whose content it rendered itself,
        and a log of pre-rendered Rich output has no character positions left to
        offer. The whole conversation was selectable-looking and copied nothing.
        """
        conversation = self._conversation()
        conversation.mount(widget)
        conversation.call_after_refresh(conversation.scroll_end, animate=False)

    def _load_sessions(self) -> None:
        """Rebuild the session list, marking the one being continued."""
        summaries = list_sessions(self.checkpoint_database_path)
        self._summaries = {summary.thread_id: summary for summary in summaries}
        if all(summary.thread_id != self.thread_id for summary in summaries):
            # A brand new session has no checkpoint yet, so it is not in the
            # list; showing it anyway keeps the marker from pointing at nothing.
            self._session_ids = [self.thread_id]
            items = [ListItem(Static(Text.assemble(("▸ ", "cyan"), "new session")))]
        else:
            self._session_ids = []
            items = []
        for summary in summaries:
            self._session_ids.append(summary.thread_id)
            active = summary.thread_id == self.thread_id
            when = (
                f"{_local(summary.last_active):%b %d %H:%M}"
                if summary.last_active
                else "unknown"
            )
            items.append(
                ListItem(
                    Static(
                        Text.assemble(
                            ("▸ " if active else "  ", "cyan"),
                            (summary.title, "bold" if active else ""),
                            ("\n    ", ""),
                            (f"{summary.turns} turns · {when}", "dim"),
                        )
                    )
                )
            )
        listing = self.query_one("#sessions", ListView)
        listing.clear()
        listing.extend(items)
        # Highlight the session being continued. Rebuilding the list clears the
        # highlight, and a list with nothing highlighted answers "which one?"
        # with silence when a key acts on the selection.
        if self.thread_id in self._session_ids:
            listing.index = self._session_ids.index(self.thread_id)
        self.sub_title = f"session {self.thread_id[:8]}"

    def _load_conversation(self) -> None:
        """Replay the active session, so switching shows what will be continued."""
        self._conversation().remove_children()
        prompt = self.query_one("#prompt", PromptInput)
        prompt.history = []
        transcript = session_transcript(self.checkpoint_database_path, self.thread_id)
        if not transcript:
            self._say(
                Static(Text("New session. Type /help for commands.", style="dim"))
            )
            return
        for role, text in transcript:
            if role == "you":
                self._write_request(text)
                prompt.remember(text)
            else:
                self._say(Markdown(text))
        prompt.remember("")

    def _write_request(self, request: str) -> None:
        self._say(
            Static(Text.assemble(("› ", "bold cyan"), (request, "bold")), classes="ask")
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Switching sessions changes what the next turn continues from."""
        index = self.query_one("#sessions", ListView).index
        if index is None or index >= len(self._session_ids):
            return
        chosen = self._session_ids[index]
        if chosen == self.thread_id:
            return
        self.thread_id = set_active_session(self.session_file_path, chosen)
        self._load_sessions()
        self._load_conversation()
        self.action_refresh_activity()
        self.query_one("#prompt", PromptInput).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        event.input.value = ""
        if not query:
            return
        self.query_one("#prompt", PromptInput).remember(query)
        self._handle(query)

    def _handle(self, query: str) -> None:
        """Answer what the interface owns; send everything else to the graph."""
        parsed = read_command(query)
        if isinstance(parsed, Rejected):
            self._say(Static(Text(parsed.message, style="yellow")))
            return
        if isinstance(parsed, SelfHandled):
            if parsed.name == "exit":
                self.exit()
            elif parsed.name == "help":
                self._say(Static(command_table(parsed.argument)))
            elif parsed.name == "session":
                self._say(
                    Static(Text(f"Current session: {self.thread_id}", style="dim"))
                )
            elif parsed.name == "new":
                self.thread_id = start_new_session(self.session_file_path)
                self._load_sessions()
                self._load_conversation()
                self.action_refresh_activity()
            else:
                self._show_evidence(parsed.argument)
            return

        self._write_request(query)
        self._begin(working_label(parsed.mode))
        self._ask(parsed.mode, parsed.request)

    @work(exclusive=True)
    async def _ask(self, mode: str, request: str) -> None:
        """Run one turn without freezing the interface."""
        try:
            result = await run_turn(
                self.graph,
                {"messages": [HumanMessage(content=request)], "mode": mode},
                {"configurable": {"thread_id": self.thread_id}},
                on_step=self._show_step,
            )
        except Exception as error:  # noqa: BLE001 - a failed turn is not a crash
            self._finish()
            self._say(Static(Text(f"⚠ {error}", style="red")))
            return
        self._finish()

        if not result.get("request_succeeded", True):
            message = result.get("request_error") or REQUEST_FAILURE_MESSAGE
            self._say(Static(Text(f"⚠ {message}", style="red")))
            self.action_refresh_activity()
            return

        answer = str(result["messages"][-1].text)
        # Show the answer before archiving it: work already done must not be
        # lost because the local archive write failed.
        self._say(Markdown(answer))
        self.knowledge_repository.add_exchange(
            thread_id=self.thread_id,
            request=request,
            answer=answer,
            selected_mode=result.get("selected_mode", mode),
        )
        self._load_sessions()
        self.action_refresh_activity()

    def _begin(self, label: str) -> None:
        self._label = label
        self._step = ""
        self._started_at = monotonic()
        self.query_one("#prompt", PromptInput).disabled = True
        self._tick()
        self._elapsed_timer = self.set_interval(0.2, self._tick)

    def _show_step(self, node: str) -> None:
        """Name the graph node now running, so a long wait reads as progress.

        Called from the worker as each node starts. Only the label changes;
        the timer keeps the display moving between steps, which matters when
        one of them is a model call lasting twenty seconds.
        """
        self._step = phase_label(node)
        self._tick()

    def _tick(self) -> None:
        elapsed = monotonic() - self._started_at
        # A frame that advances every tick is what distinguishes working from
        # hung at a glance; the elapsed time alone reads as a static number
        # unless you watch it.
        frame = SPINNER_FRAMES[int(elapsed * 5) % len(SPINNER_FRAMES)]
        step = f" · {self._step}" if self._step else ""
        self.query_one("#status", Static).update(
            Text(f"{frame} {self._label}{step} … {elapsed:.1f}s", style="bold cyan")
        )

    def _finish(self) -> None:
        if self._elapsed_timer is not None:
            self._elapsed_timer.stop()
            self._elapsed_timer = None
        self.query_one("#status", Static).update("")
        prompt = self.query_one("#prompt", PromptInput)
        prompt.disabled = False
        prompt.focus()

    # -- activity ------------------------------------------------------------
    def action_refresh_activity(self) -> None:
        """Reload the traces, which arrive after the turn that produced them."""
        self._show_phoenix()
        self._traces = recent_traces(
            self.trace_database_path,
            TRACE_LIMIT,
            thread_id=None if self._every_session else self.thread_id,
        )
        self._spans = {}
        self._evidence_ids = self._correlate_evidence(self._traces)

        table = self.query_one("#turns", DataTable)
        table.clear()
        for trace in self._traces:
            # Runtime text — what the user typed, what the trace recorded — is
            # added as `Text`, never as a bare string: DataTable runs string
            # cells through rich's markup parser, so a request mentioning
            # "[/var/log/syslog]" would raise MarkupError in the table's idle
            # handler and take the whole app down. This is the same discipline
            # the command line documents in `run_chat`.
            table.add_row(
                f"{_local(trace.started_at):%b %d %H:%M}",
                Text(trace.request or "—"),
                Text(trace.mode or "auto"),
                f"{trace.elapsed_seconds:.1f}s",
                f"{trace.prompt_tokens:,}",
                Text("failed", style="red") if trace.failed else "ok",
                key=trace.trace_id,
            )
        self._update_summary()
        self._show_spans(0 if self._traces else None)

    def _update_summary(self) -> None:
        scope = (
            "all sessions" if self._every_session else f"session {self.thread_id[:8]}"
        )
        if not self._traces:
            newest = newest_trace_at(self.trace_database_path)
            message = (
                NO_TRACES
                if newest is None
                else NO_TRACES_THIS_SESSION.format(
                    age=_age(newest),
                    when=f"{_local(newest):%b %d %H:%M}",
                )
            )
            self.query_one("#summary", Static).update(Text(message, style="yellow"))
            return
        elapsed = sum(trace.elapsed_seconds for trace in self._traces)
        tokens = sum(trace.prompt_tokens for trace in self._traces)
        failed = sum(1 for trace in self._traces if trace.failed)
        # A session showing one run out of thirty-five looks exactly like a
        # store holding one run, and the reader has no way to tell which. The
        # hint used to appear only when the table was empty, which is the one
        # case where it was least needed.
        elsewhere = (
            0
            if self._every_session
            else trace_count(self.trace_database_path) - len(self._traces)
        )
        others = f" · {elsewhere} more in other sessions (a)" if elsewhere > 0 else ""
        # Runs and turns are not the same set, and a reader reasonably assumes
        # they are. A failed run is removed from the conversation but keeps its
        # trace, and a turn taken while the collector was down is in the
        # conversation with no trace at all — so a chat and its activity can
        # legitimately share nothing. Showing both counts is what makes that
        # visible instead of looking like the wrong session is displayed.
        summary = self._summaries.get(self.thread_id)
        turns = (
            f"{_count(summary.turns, 'turn')} · "
            if summary is not None and not self._every_session
            else ""
        )
        self.query_one("#summary", Static).update(
            Text(
                f"{scope} · {turns}{_count(len(self._traces), 'traced run')} · "
                f"{elapsed:.1f}s · {tokens:,} prompt tokens · {failed} failed{others}"
            )
        )

    def _correlate_evidence(self, traces: list[Trace]) -> dict[str, str]:
        """Match stored evidence to the run that produced it.

        A report names the conversation it was collected for but not the
        individual turn, so the conversation narrows the candidates and the
        time picks the turn: a report is written during the run that fetched
        it. Reports written before the conversation was recorded have none to
        compare, so for them the time is all there is.
        """
        if self.threat_report_store is None:
            return {}
        reports = self.threat_report_store.recent(REPORT_LIMIT)
        matched = {}
        for trace in traces:
            for report in reports:
                if report.thread_id not in (None, trace.thread_id):
                    continue
                offset = (report.created_at - trace.started_at).total_seconds()
                # A filename keeps whole seconds, so a report written a fraction
                # of a second into a run dates from just before it started.
                # Without that allowance the `e` key is silently not offered,
                # which reads exactly like a run that stored nothing.
                if -TIMESTAMP_RESOLUTION_SECONDS <= offset <= trace.elapsed_seconds:
                    matched[trace.trace_id] = report.report_id
                    break
        return matched

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """The detail pane follows the selected turn rather than standing still."""
        self._show_spans(event.cursor_row)

    def _selected_trace(self) -> Trace | None:
        if not self._traces:
            return None
        index = self.query_one("#turns", DataTable).cursor_row
        if index is None or not 0 <= index < len(self._traces):
            return None
        return self._traces[index]

    def _spans_of(self, trace: Trace) -> list[Span]:
        if trace.trace_id not in self._spans:
            self._spans[trace.trace_id] = spans_for_trace(
                self.trace_database_path, trace.trace_id
            )
        return self._spans[trace.trace_id]

    def _show_spans(self, index: int | None) -> None:
        detail = self.query_one("#span-detail", VerticalScroll)
        detail.remove_children()
        detail.mount(Static(self._span_detail(index)))

    def _span_detail(self, index: int | None) -> Text:
        """The selected run, as one block of selectable text.

        Built as a single `Text` rather than written line by line so the whole
        pane can be dragged across. A span name and its timing are exactly what
        gets pasted into a note about a slow run.
        """
        if index is None or not 0 <= index < len(self._traces):
            return Text("Nothing to show.", style="dim")
        trace = self._traces[index]
        lines = [
            Text(trace.request or trace.trace_id[:12], style="bold"),
            Text(""),
        ]
        for span in self._spans_of(trace):
            tokens = f"{span.prompt_tokens:,}" if span.prompt_tokens else "—"
            failed = span.status.upper() == "ERROR"
            lines.append(
                Text.assemble(
                    ("  " * span.depth, ""),
                    (span.name, "red" if failed else "cyan"),
                    (f" {span.kind.lower()}", "dim"),
                    (f" {span.elapsed_seconds:.2f}s {tokens}", ""),
                )
            )
        lines.append(Text(""))
        if self._evidence_ids.get(trace.trace_id):
            lines.append(
                Text.assemble(
                    ("e", "bold"),
                    (f"  evidence {self._evidence_ids[trace.trace_id]}", "dim"),
                )
            )
        lines.append(
            Text.assemble(("p", "bold"), ("  prompts sent to the model", "dim"))
        )
        if self.phoenix_url:
            lines.append(Text(f"Deep trace: {self.phoenix_url}", style="dim"))
        return Text("\n").join(lines)

    # -- actions -------------------------------------------------------------
    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def on_tabbed_content_tab_activated(
        self,
        event: TabbedContent.TabActivated,
    ) -> None:
        """Move focus with the tab, so each tab's keys work on arrival.

        Left alone, focus stays in the chat box, where every single-letter
        binding is swallowed as typing and the activity tab looks unresponsive.
        """
        chat = event.pane.id == "chat"
        self.query_one("#prompt" if chat else "#turns").focus()
        self.refresh_bindings()

    def _show_phoenix(self) -> None:
        """Put the current Phoenix state on screen.

        Reads launchd rather than remembering what the last action did, because
        the service can also be started, stopped or crash outside this
        interface, and a remembered answer would then be confidently wrong.
        """
        state = phoenix_state(self.phoenix_paths)
        style = {
            PHOENIX_RUNNING: "green",
            PHOENIX_STOPPED: "yellow",
            PHOENIX_NOT_INSTALLED: "dim",
        }[state]
        hint = "" if state == PHOENIX_NOT_INSTALLED else "   s  on/off    r  restart"
        self.query_one("#services", Static).update(
            Text.assemble(("Phoenix: ", "dim"), (state, style), (hint, "dim"))
        )

    def action_phoenix_toggle(self) -> None:
        """Start Phoenix if it is stopped, stop it if it is running."""
        state = phoenix_state(self.phoenix_paths)
        if state == PHOENIX_NOT_INSTALLED:
            self.notify("Phoenix is not installed. Run: orisctl phoenix install")
            return
        if state == PHOENIX_RUNNING:
            self._control_phoenix(stop_service, "stop", "stopped")
        else:
            self._control_phoenix(start_service, "start", "started")

    def action_phoenix_restart(self) -> None:
        """Restart Phoenix, or start it if launchd is not running it."""
        if phoenix_state(self.phoenix_paths) == PHOENIX_NOT_INSTALLED:
            self.notify("Phoenix is not installed. Run: orisctl phoenix install")
            return
        self._control_phoenix(restart_service, "restart", "restarted")

    @work(thread=True)
    def _control_phoenix(self, action: Any, verb: str, done: str) -> None:
        """Run one launchctl command off the UI thread.

        `launchctl` is a subprocess and takes long enough to be noticed, so
        calling it inline freezes the interface mid-keystroke. Failure is
        reported and nothing else: tracing is optional, and a service that will
        not start is not a reason for the conversation to stop working.
        """
        paths = self.phoenix_paths
        assert paths is not None  # guarded by the state check in both callers
        try:
            action(paths)
        except Exception as error:  # noqa: BLE001 - a dead service is not a crash
            self.call_from_thread(self.notify, f"Phoenix would not {verb}: {error}")
        else:
            self.call_from_thread(self.notify, f"Phoenix {done}.")
        self.call_from_thread(self._show_phoenix)

    def action_toggle_scope(self) -> None:
        """Widen the activity view to every session, or narrow it back."""
        self._every_session = not self._every_session
        self.action_refresh_activity()

    def action_open_prompts(self) -> None:
        trace = self._selected_trace()
        if trace is None:
            self.notify("No run selected.")
            return
        self.push_screen(
            PromptScreen(
                trace.request or trace.trace_id[:12],
                system_prompts_for_trace(self.trace_database_path, trace.trace_id),
            )
        )

    def action_open_evidence(self) -> None:
        trace = self._selected_trace()
        report_id = self._evidence_ids.get(trace.trace_id) if trace else None
        self._show_evidence(report_id or "")

    def _show_evidence(self, report_id: str) -> None:
        """Open one stored report, newest when no ID is given.

        Evidence is always shown from the activity tab, including when asked for
        by `/threat show` in the chat. One place is responsible for it, and that
        place is the one that lists the runs evidence belongs to.
        """
        if self.threat_report_store is None:
            self.notify("Stored evidence reports are not configured.")
            return
        store = self.threat_report_store
        document = store.load(report_id) if report_id else store.latest()
        if document is None:
            missing = f"No stored report {report_id!r}." if report_id else "No reports."
            self.notify(f"{missing} Reports are kept {store.retention_days} days.")
            return
        self.action_show_tab("activity")
        self._select_run_for(str(document.get("report_id") or ""))
        # Let the tab switch settle first: pushing a modal in the same frame
        # leaves the tab change half-applied, and the interface is back on the
        # chat tab when the viewer is closed.
        self.call_after_refresh(self.push_screen, EvidenceScreen(document))

    def _select_run_for(self, report_id: str) -> None:
        """Point the activity table at the run that produced this evidence.

        Otherwise the tab revealed underneath the viewer describes a different
        run from the one on screen.
        """
        for index, trace in enumerate(self._traces):
            if self._evidence_ids.get(trace.trace_id) == report_id:
                self.query_one("#turns", DataTable).move_cursor(row=index)
                return

    def _on_activity(self) -> bool:
        return self.query_one(TabbedContent).active == "activity"

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Offer a key only on the tab where it means something.

        This is also what stops a single letter being stolen from the chat box,
        and it keeps the footer honest: the activity keys are not advertised
        while the conversation is on screen.
        """
        if action in ACTIVITY_ONLY_ACTIONS:
            return self._on_activity()
        if action in CHAT_ONLY_ACTIONS:
            return not self._on_activity()
        return True

    def action_delete_session(self) -> None:
        """Delete the highlighted conversation, after confirming what goes."""
        index = self.query_one("#sessions", ListView).index
        if index is None or index >= len(self._session_ids):
            return
        thread_id = self._session_ids[index]
        summary = self._summaries.get(thread_id)
        if summary is None:
            self.notify("That session has nothing stored yet.")
            return
        archived = self.knowledge_repository.count_by_source_ref(thread_id)
        self.push_screen(
            ConfirmDeleteScreen(
                summary.title, summary.turns, archived, self._report_count(thread_id)
            ),
            lambda confirmed: self._delete_session(thread_id) if confirmed else None,
        )

    def _report_count(self, thread_id: str) -> int:
        """How much stored evidence this conversation is about to take with it."""
        if self.threat_report_store is None:
            return 0
        return sum(
            1
            for report in self.threat_report_store.recent(REPORT_LIMIT)
            if report.thread_id == thread_id
        )

    def _delete_session(self, thread_id: str) -> None:
        delete_session(self.checkpoint_database_path, thread_id)
        removed = self.knowledge_repository.delete_by_source_ref(thread_id)
        # The evidence goes too. These are the most sensitive files ORIS writes
        # — every indicator investigated and everything the providers returned
        # — and they are exactly what someone deleting a conversation means.
        reports = (
            self.threat_report_store.delete_for_thread(thread_id)
            if self.threat_report_store is not None
            else 0
        )
        # Deleting the conversation being continued leaves nothing to continue,
        # so the interface starts a fresh one rather than pointing at a gap.
        if thread_id == self.thread_id:
            self.thread_id = start_new_session(self.session_file_path)
        self._load_sessions()
        self._load_conversation()
        self.action_refresh_activity()
        self.notify(
            f"Deleted the conversation, {removed} archived answers, "
            f"and {reports} evidence reports."
        )

    def action_export(self) -> None:
        """Write the visible activity to JSON, for a ticket or a spreadsheet."""
        if self.export_directory is None or not self._traces:
            self.notify("Nothing to export.")
            return
        payload = [
            {
                "trace_id": trace.trace_id,
                "started_at": trace.started_at.isoformat(),
                "request": trace.request,
                "mode": trace.mode,
                "thread_id": trace.thread_id,
                "elapsed_seconds": round(trace.elapsed_seconds, 3),
                "prompt_tokens": trace.prompt_tokens,
                "error_count": trace.error_count,
                "evidence_report_id": self._evidence_ids.get(trace.trace_id),
                "spans": [
                    {
                        "name": span.name,
                        "kind": span.kind,
                        "depth": span.depth,
                        "elapsed_seconds": round(span.elapsed_seconds, 3),
                        "status": span.status,
                        "prompt_tokens": span.prompt_tokens,
                    }
                    for span in self._spans_of(trace)
                ],
            }
            for trace in self._traces
        ]
        self.export_directory.mkdir(parents=True, exist_ok=True)
        path = self.export_directory / (
            f"activity-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        )
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.notify(f"Exported {len(payload)} runs to {path}")


async def _main() -> None:
    """Build ORIS and run its terminal interface."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from oris.sessions import ACTIVE_SESSION_FILENAME, load_or_create_session
    from oris.web_research_app import (
        build_oris_graph,
        knowledge_repository,
        settings,
        threat_report_store,
    )

    database_path = settings.checkpoint_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    session_file_path = database_path.parent / ACTIVE_SESSION_FILENAME

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        graph = await build_oris_graph(checkpointer)
        await OrisTui(
            graph,
            knowledge_repository,
            thread_id=load_or_create_session(session_file_path),
            session_file_path=session_file_path,
            checkpoint_database_path=database_path,
            trace_database_path=settings.trace_database_path,
            threat_report_store=threat_report_store,
            export_directory=settings.export_directory,
            phoenix_url=settings.phoenix_url,
            # Resolved from the package rather than the working directory, so
            # the keys work wherever the interface was started from. Only the
            # service label and the installed plist are read here; the project
            # paths matter to `orisctl install`, which this never does.
            phoenix_paths=LaunchAgentPaths.from_project_root(
                Path(__file__).resolve().parents[2], "phoenix"
            ),
        ).run_async()


def main() -> None:
    """Start the terminal interface."""
    import asyncio

    asyncio.run(_main())
