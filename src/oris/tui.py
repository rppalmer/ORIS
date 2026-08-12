"""Placeholder terminal interface for ORIS, for judging the shape before building it.

Nothing here is wired up. No graph is compiled, no model is called, no database
is read: every value on screen is invented, so the layout can be argued with
before any of it is made real.

Deliberately self-contained. It has its own entry point, imports nothing from
`oris.cli`, and changes nothing the `oris` command uses. Removing it means
deleting this file, the `tui` extra, and the `oris-tui` script.
"""

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

BANNER = "ORIS · Orchestrator / Research / Analysis"

# Invented. Shaped like a real turn so the columns can be judged, not trusted.
SAMPLE_ACTIVITY = [
    ("14:02:11", "/threat enrich 45.83.192.4", "Threat Intel", "24.1s", "2,957", "ok"),
    (
        "13:58:40",
        "/research langgraph 1.2 errors",
        "Web Research",
        "18.6s",
        "1,204",
        "ok",
    ),
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

SAMPLE_SPANS = [
    ("validate_request", "CHAIN", "0.00s", "-"),
    ("extract_indicators", "CHAIN", "0.26s", "-"),
    ("extract_iocs", "TOOL", "0.26s", "-"),
    ("plan_investigation", "CHAIN", "0.00s", "-"),
    ("collect_evidence", "CHAIN", "2.35s", "-"),
    ("enrich", "TOOL", "2.35s", "-"),
    ("synthesize_answer", "CHAIN", "20.09s", "2,957"),
    ("ChatOpenAI", "LLM", "20.09s", "2,957"),
    ("validate_sources", "CHAIN", "0.00s", "-"),
]

PLACEHOLDER_CONVERSATION = """\
[dim]— placeholder, nothing is wired up —[/]

[bold cyan]You ›[/] /threat enrich 45.83.192.4

[bold green]ORIS[/]
Censys identifies 22 exposed services including pure-ftpd, OpenSSH and Exim.
AbuseIPDB reports confidence 0/100 across 0 reports. Sentinel has no record of
this address, returning a default allow verdict rather than an assessment.

[dim]Full evidence: a3f21c — /threat show a3f21c[/]
"""


class OrisTui(App):
    """Two-pane shell: the conversation, and what it cost to produce."""

    TITLE = "ORIS"
    SUB_TITLE = "Orchestrator / Research / Analysis"
    CSS = """
    Screen { layers: base; }
    #conversation { height: 1fr; border: round $panel; padding: 0 1; }
    #prompt { dock: bottom; }
    /* The turn list carries six columns and the detail pane three, so an even
       split truncates the headings that matter at ordinary widths. */
    #activity-table { width: 3fr; height: 1fr; }
    #span-detail { width: 2fr; height: 1fr; border: round $panel; padding: 0 1; }
    #summary { height: auto; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "show_tab('chat')", "Chat"),
        ("2", "show_tab('activity')", "Activity"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"):
                yield RichLog(id="conversation", markup=True, wrap=True)
                yield Input(placeholder="Ask, or /help …", id="prompt")
            with TabPane("Activity", id="activity"):
                yield Static(
                    "Turns this session: 4 · 83.6s total · 8,383 tokens · 1 failed",
                    id="summary",
                )
                with Horizontal():
                    yield DataTable(id="activity-table", cursor_type="row")
                    yield RichLog(id="span-detail", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#conversation", RichLog).write(PLACEHOLDER_CONVERSATION)

        turns = self.query_one("#activity-table", DataTable)
        turns.add_columns("Time", "Request", "Path", "Elapsed", "Tokens", "Status")
        for row in SAMPLE_ACTIVITY:
            turns.add_row(*row)

        self._show_spans()

    def _show_spans(self) -> None:
        detail = self.query_one("#span-detail", RichLog)
        detail.clear()
        detail.write("[bold]Trace fbc1f791[/]  [dim]/threat enrich 45.83.192.4[/]\n")
        for name, kind, elapsed, tokens in SAMPLE_SPANS:
            detail.write(
                f"[cyan]{name:<20}[/] [dim]{kind:<6}[/] {elapsed:>7}  [dim]{tokens}[/]"
            )
        detail.write("\n[dim]Deep trace: http://127.0.0.1:6006[/]")

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab


def main() -> None:
    """Run the placeholder interface."""
    OrisTui().run()
