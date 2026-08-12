"""Placeholder terminal interface for ORIS, for judging the shape before building it.

Nothing here is wired up. No graph is compiled, no model is called, no database
is read: every value on screen is invented, so the layout and the interactions
can be argued with before any of it is made real.

Deliberately self-contained. It has its own entry point, imports nothing from
the rest of ORIS, and changes nothing the `oris` command uses. Removing it means
deleting this file, the `tui` extra, the `oris-tui` script, and its test.
"""

import json

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

# Everything below is invented, shaped like the real thing so the columns and
# the interactions can be judged rather than trusted.

SESSIONS = [
    ("ddb8754f", "threat triage 45.83.192.4", "12 turns", "2m ago", True),
    ("7c21a99e", "langgraph error handlers", "31 turns", "yesterday", False),
    ("1f04bb30", "scheduling decisions", "8 turns", "3 days ago", False),
]

TURNS = [
    ("14:02:11", "/threat enrich 45.83.192.4", "Threat Intel", "24.1s", "2,957", "ok"),
    ("13:58:40", "/research langgraph errors", "Web Research", "18.6s", "1,204", "ok"),
    (
        "13:51:02",
        "what did we decide on scheduling",
        "Local Knowledge",
        "9.2s",
        "812",
        "ok",
    ),
    (
        "13:44:19",
        "/community langgraph",
        "Community Research",
        "31.7s",
        "3,410",
        "failed",
    ),
]

SPANS_BY_TURN = {
    0: [
        ("validate_request", "CHAIN", "0.00s", "-"),
        ("extract_indicators", "CHAIN", "0.26s", "-"),
        ("extract_iocs", "TOOL", "0.26s", "-"),
        ("plan_investigation", "CHAIN", "0.00s", "-"),
        ("collect_evidence", "CHAIN", "2.35s", "-"),
        ("enrich", "TOOL", "2.35s", "-"),
        ("synthesize_answer", "CHAIN", "20.09s", "2,957"),
        ("ChatOpenAI", "LLM", "20.09s", "2,957"),
        ("validate_sources", "CHAIN", "0.00s", "-"),
    ],
    1: [
        ("validate_request", "CHAIN", "0.00s", "-"),
        ("plan_search", "CHAIN", "3.10s", "402"),
        ("search_web", "TOOL", "1.44s", "-"),
        ("synthesize_answer", "CHAIN", "14.02s", "802"),
        ("validate_answer", "CHAIN", "0.00s", "-"),
    ],
    2: [
        ("plan_search", "CHAIN", "2.80s", "310"),
        ("retrieve_knowledge", "CHAIN", "0.04s", "-"),
        ("answer_from_knowledge", "CHAIN", "6.31s", "502"),
    ],
    3: [
        ("validate_request", "CHAIN", "0.00s", "-"),
        ("collect_evidence", "TOOL", "28.4s", "-"),
        ("synthesize_answer", "CHAIN", "3.30s", "3,410"),
        ("validate_citations", "CHAIN", "0.00s", "FAILED"),
    ],
}

EVIDENCE_IDS = {0: "a3f21c", 3: "9b7e40"}

SAMPLE_EVIDENCE = {
    "45.83.192.4": {
        "data": {
            "sources": {
                "censys": {"ok": True, "data": {"service_count": 22}},
                "sentinel": {"ok": True, "data": {"known": False, "risk_score": 0}},
                "shodan": {"ok": False, "code": "not_found"},
            }
        }
    }
}

CONVERSATION = """\
[dim]— placeholder, nothing is wired up —[/]

[bold cyan]›[/] /threat enrich 45.83.192.4

  Censys identifies 22 exposed services including pure-ftpd, OpenSSH and Exim.
  AbuseIPDB reports confidence 0/100 across 0 reports. Sentinel has no record
  of this address, returning a default allow verdict rather than an assessment.

  [dim]10 sources · 24.1s · 2,957 tokens · evidence a3f21c[/]

[bold cyan]›[/] which of those services is oldest?

  Exim 4.96.2 is the oldest of the identified versions.
"""


class EvidenceScreen(ModalScreen):
    """Full-screen evidence viewer, reached by selecting a turn rather than typing an ID."""

    BINDINGS = [("escape,q", "dismiss", "Close")]
    CSS = """
    EvidenceScreen { align: center middle; }
    #evidence { width: 80%; height: 80%; border: round $accent; padding: 1 2; }
    """

    def __init__(self, report_id: str) -> None:
        super().__init__()
        self.report_id = report_id

    def compose(self) -> ComposeResult:
        log = RichLog(id="evidence", markup=True, wrap=True)
        yield log

    def on_mount(self) -> None:
        log = self.query_one("#evidence", RichLog)
        log.write(f"[bold]Evidence {self.report_id}[/]  [dim]esc to close[/]\n")
        log.write(json.dumps(SAMPLE_EVIDENCE, indent=2))


class OrisTui(App):
    """Conversation on one tab, what it cost on the other."""

    TITLE = "ORIS"
    SUB_TITLE = "Orchestrator / Research / Analysis"
    CSS = """
    #sessions { width: 32; border: round $panel; }
    #conversation { height: 1fr; border: round $panel; padding: 0 1; }
    #prompt { dock: bottom; }
    /* The turn list carries six columns and the detail pane three, so an even
       split truncates the headings that matter at ordinary widths. */
    #turns { width: 3fr; height: 1fr; }
    #span-detail { width: 2fr; height: 1fr; border: round $panel; padding: 0 1; }
    #summary { height: auto; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("1", "show_tab('chat')", "Chat"),
        Binding("2", "show_tab('activity')", "Activity"),
        Binding("e", "open_evidence", "Evidence"),
        Binding("x", "export", "Export"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"), Horizontal():
                yield ListView(
                    *[
                        ListItem(
                            Static(
                                f"{'▸ ' if active else '  '}{title}\n"
                                f"    [dim]{turns} · {when}[/]",
                                markup=True,
                            )
                        )
                        for _sid, title, turns, when, active in SESSIONS
                    ],
                    id="sessions",
                )
                with Vertical():
                    yield RichLog(id="conversation", markup=True, wrap=True)
                    yield Input(placeholder="Ask, or /help …", id="prompt")
            with TabPane("Activity", id="activity"):
                yield Static("", id="summary")
                with Horizontal():
                    yield DataTable(id="turns", cursor_type="row")
                    yield RichLog(id="span-detail", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#conversation", RichLog).write(CONVERSATION)
        self.query_one("#summary", Static).update(
            "Session ddb8754f · 4 turns · 83.6s · 8,383 tokens · 1 failed"
        )
        table = self.query_one("#turns", DataTable)
        table.add_columns("Time", "Request", "Path", "Elapsed", "Tokens", "Status")
        for row in TURNS:
            table.add_row(*row)
        self._show_spans(0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """The detail pane follows the selected turn rather than standing still."""
        self._show_spans(event.cursor_row)

    def _show_spans(self, index: int) -> None:
        detail = self.query_one("#span-detail", RichLog)
        detail.clear()
        request = TURNS[index][1]
        detail.write(f"[bold]{request}[/]\n")
        for name, kind, elapsed, tokens in SPANS_BY_TURN.get(index, []):
            marker = "[red]" if tokens == "FAILED" else "[cyan]"
            detail.write(
                f"{marker}{name:<20}[/] [dim]{kind:<6}[/] {elapsed:>7}  [dim]{tokens}[/]"
            )
        report_id = EVIDENCE_IDS.get(index)
        if report_id:
            detail.write(f"\n[bold]e[/] [dim]open evidence {report_id}[/]")
        detail.write("[dim]Deep trace: http://127.0.0.1:6006[/]")

    def _selected_turn(self) -> int:
        return self.query_one("#turns", DataTable).cursor_row

    def action_open_evidence(self) -> None:
        report_id = EVIDENCE_IDS.get(self._selected_turn())
        if report_id:
            self.push_screen(EvidenceScreen(report_id))

    def action_export(self) -> None:
        self.notify("Export: this turn / this session / activity as JSON or CSV")

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab


def main() -> None:
    """Run the placeholder interface."""
    OrisTui().run()
