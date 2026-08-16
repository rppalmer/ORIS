"""The terminal interface, driven through Textual's own test pilot.

Skips entirely when the optional `tui` extra is not installed, so an install
without it still has a clean test run.
"""

import asyncio
import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from checkpoint_fixture import write_session
from langchain_core.messages import AIMessage
from phoenix_fixture import write_trace

pytest.importorskip("textual", reason="install the optional 'tui' extra")

from textual.widgets import DataTable, ListView, TabbedContent  # noqa: E402

from oris.knowledge import KnowledgeRepository  # noqa: E402
from oris.sessions import list_sessions  # noqa: E402
from oris.threat_reports import ThreatReportStore  # noqa: E402
from oris.tui import (  # noqa: E402
    ConfirmDeleteScreen,
    EvidenceScreen,
    OrisTui,
    PromptScreen,
)

THREAD_ID = "thread-1"
STARTED_AT = datetime(2026, 8, 12, 2, 4, 29, tzinfo=UTC)
ANSWER = "Censys reports 22 exposed services."
# An unmatched closing tag, as far as rich's markup parser is concerned.
BRACKETED_PATH = "tail [/var/log/syslog]"


class FakeGraph:
    """Stands in for the compiled graph: records the call, streams a result.

    Streamed rather than awaited because the interface now reports each node as
    it starts, which needs an async iterator. `steps` are the node names to
    announce before the answer arrives.
    """

    def __init__(
        self,
        result: dict[str, Any] | Exception,
        steps: tuple[str, ...] = (),
    ) -> None:
        self.result = result
        self.steps = steps
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def astream(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
        **kwargs: Any,
    ) -> AsyncIterator[tuple[tuple[str, ...], str, Any]]:
        self.calls.append((state, config))
        if isinstance(self.result, Exception):
            raise self.result
        for name in self.steps:
            yield (
                ("specialist:1",),
                "debug",
                {"type": "task", "payload": {"name": name}},
            )
        yield ((), "values", self.result)


class FakeKnowledge(KnowledgeRepository):
    """The real archiving rules over an in-memory store.

    Subclassed rather than reimplemented, and deliberately skipping the base
    initializer so no database is opened: what decides whether a turn is
    archived, and what shape it takes, is then the production code rather than
    a copy of it that can drift.
    """

    def __init__(self) -> None:
        self.documents: list[Any] = []

    def add(self, document: Any) -> None:
        self.documents.append(document)

    def count_by_source_ref(self, source_ref: str) -> int:
        return sum(1 for d in self.documents if d.source_ref == source_ref)

    def delete_by_source_ref(self, source_ref: str) -> int:
        removed = self.count_by_source_ref(source_ref)
        self.documents = [d for d in self.documents if d.source_ref != source_ref]
        return removed


def _answered(text: str = ANSWER) -> dict[str, Any]:
    return {"messages": [AIMessage(content=text)], "request_succeeded": True}


def _build(
    tmp_path: Path,
    *,
    result: dict[str, Any] | Exception | None = None,
    traces: bool = True,
    evidence: bool = False,
    request: str = "enrich 8.8.8.8",
    started_at: datetime = STARTED_AT,
    steps: tuple[str, ...] = (),
) -> tuple[OrisTui, FakeGraph, FakeKnowledge]:
    trace_database_path = tmp_path / "phoenix.db"
    if traces:
        write_trace(
            trace_database_path,
            trace_id="aaa",
            request=request,
            thread_id=THREAD_ID,
            started_at=started_at,
            elapsed_seconds=19.5,
            system_prompt="You are ORIS's Threat Intel specialist.",
        )
        write_trace(
            trace_database_path,
            trace_id="bbb",
            request="someone else's question",
            thread_id="other-thread",
            started_at=STARTED_AT,
        )

    store = ThreatReportStore(tmp_path / "reports", retention_days=30)
    if evidence:
        # Written two seconds into the run, which is how it is matched back.
        store.save(
            "enrich 8.8.8.8",
            {"enrich": {"data": {"sources": {"censys": {"ok": True}}}}},
            thread_id=THREAD_ID,
            now=STARTED_AT.replace(second=31),
        )

    graph = FakeGraph(result if result is not None else _answered(), steps)
    knowledge = FakeKnowledge()
    app = OrisTui(
        graph,  # type: ignore[arg-type]
        knowledge,  # type: ignore[arg-type]
        thread_id=THREAD_ID,
        session_file_path=tmp_path / "current_session",
        checkpoint_database_path=tmp_path / "checkpoints.sqlite",
        trace_database_path=trace_database_path,
        threat_report_store=store,
        export_directory=tmp_path / "exports",
        phoenix_url="http://127.0.0.1:6006",
    )
    return app, graph, knowledge


def _text(app: OrisTui, selector: str) -> str:
    """Read back what a log-style widget has actually rendered."""
    node = app.screen.query_one(selector)
    return "\n".join(strip.text for strip in node.lines)


def _rows(app: OrisTui) -> str:
    """Read back the activity table, cell by cell."""
    table = app.query_one("#turns", DataTable)
    return "\n".join(
        " ".join(str(cell) for cell in table.get_row_at(index))
        for index in range(table.row_count)
    )


def _drawn(app: OrisTui, selector: str) -> str:
    """Read back what a widget actually draws, not what it was handed.

    `get_row_at` returns the stored cell, so it cannot see a value the table
    fails to render. Only asking the widget for its lines exercises that.
    """
    node = app.screen.query_one(selector)
    return "\n".join(node.render_line(y).text for y in range(node.size.height))


def _painted(app: OrisTui) -> str:
    """Return the text actually composited onto the screen.

    A widget can render its line correctly and still be covered by a sibling,
    which `_drawn` cannot see because it asks the widget rather than the
    screen. The export splits a line across style spans and writes spaces as
    entities, so the markup has to come off before anything can be matched.
    """
    body = app.export_screenshot().split("</style>")[-1]
    return html.unescape(re.sub(r"<[^>]+>", "", body)).replace("\xa0", " ")


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


async def _ask(app: OrisTui, pilot: Any, request: str) -> None:
    await pilot.press(*request, "enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_a_question_reaches_the_graph_and_the_answer_is_shown(tmp_path: Path) -> None:
    """The whole point of wiring it up: one typed line, one real answer."""

    async def drive() -> tuple[str, FakeGraph, FakeKnowledge]:
        app, graph, knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "hello")
            return _text(app, "#conversation"), graph, knowledge

    conversation, graph, knowledge = _run(drive())

    assert "Censys reports 22 exposed services" in conversation
    state, config = graph.calls[0]
    assert state["mode"] == "auto"
    assert config["configurable"]["thread_id"] == THREAD_ID
    # Parity with the command line: an answered turn joins the local archive.
    assert len(knowledge.documents) == 1


def test_a_long_turn_says_which_step_is_running(tmp_path: Path) -> None:
    """A `/threat` run measured 29 seconds, 23 of them in one model call.

    A single unchanging label for that wait cannot tell working apart from
    hung, which is what made the interface look silent. The step has to name
    the node that is running, not the one that just finished.
    """

    async def drive() -> list[str]:
        app, _, _ = _build(
            tmp_path,
            result=_answered(),
            steps=("collect_evidence", "synthesize_answer"),
        )
        seen: list[str] = []
        async with app.run_test(size=(110, 30)) as pilot:
            app._begin("Threat Intel")
            for node in ("collect_evidence", "synthesize_answer"):
                app._show_step(node)
                await pilot.pause()
                seen.append(_drawn(app, "#status"))
        return seen

    shown = _run(drive())

    assert "querying providers" in shown[0]
    assert "writing the answer" in shown[1]
    assert "Threat Intel" in shown[1]


def test_the_running_status_is_visible_on_screen(tmp_path: Path) -> None:
    """Setting the status is not the same as the reader being able to see it.

    The status and the prompt were docked to the same edge, so the three-row
    prompt was composited over the one-row status. Every assertion about the
    status passed, because the content was correct the entire time, while the
    interface showed nothing at all for the whole turn. This reads the painted
    screen rather than the widget, which is the only way that is visible.
    """

    async def drive() -> str:
        app, _, _ = _build(tmp_path, result=_answered(), steps=("synthesize_answer",))
        async with app.run_test(size=(110, 30)) as pilot:
            app._begin("Threat Intel")
            app._show_step("synthesize_answer")
            await pilot.pause()
            return _painted(app)

    screen = _run(drive())

    assert "Threat Intel" in screen
    assert "writing the answer" in screen


def test_a_slash_command_selects_the_specialist(tmp_path: Path) -> None:
    """Both front ends parse one table, so neither can support a command alone."""

    async def drive() -> FakeGraph:
        app, graph, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "/threat enrich 8.8.8.8")
            return graph

    state, _config = _run(drive()).calls[0]

    assert state["mode"] == "threat_intel"
    assert state["messages"][0].content == "enrich 8.8.8.8"


def test_a_failed_turn_is_reported_and_not_archived(tmp_path: Path) -> None:
    """A failure is not an answer, so it must not enter the searchable archive."""

    async def drive() -> tuple[str, FakeKnowledge]:
        app, _, knowledge = _build(
            tmp_path,
            result={
                "messages": [AIMessage(content="")],
                "request_succeeded": False,
                "request_error": "ThreatSyft is unreachable",
            },
        )
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "/threat enrich 8.8.8.8")
            return _text(app, "#conversation"), knowledge

    conversation, knowledge = _run(drive())

    assert "ThreatSyft is unreachable" in conversation
    assert knowledge.documents == []


def test_a_raised_error_does_not_take_the_interface_down(tmp_path: Path) -> None:
    """A model timeout should cost the turn, not the session."""

    async def drive() -> tuple[str, bool]:
        app, _, _ = _build(tmp_path, result=RuntimeError("connection refused"))
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "hello")
            usable = not app.query_one("#prompt").disabled
            return _text(app, "#conversation"), usable

    conversation, usable = _run(drive())

    assert "connection refused" in conversation
    assert usable


def test_the_up_arrow_recalls_the_previous_request(tmp_path: Path) -> None:
    """The command line has this, and losing it on the way to a TUI is a regression."""

    async def drive() -> tuple[str, str]:
        app, _, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "first question")
            await pilot.press("up")
            recalled = app.query_one("#prompt").value
            await pilot.press("down")
            return recalled, app.query_one("#prompt").value

    recalled, after = _run(drive())

    assert recalled == "first question"
    assert after == ""


def test_activity_shows_this_session_and_can_widen_to_all(tmp_path: Path) -> None:
    """Traces from another conversation are noise until they are asked for."""

    async def drive() -> tuple[str, str]:
        app, _, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            session_only = _rows(app)
            await pilot.press("a")
            await pilot.pause()
            return session_only, _rows(app)

    session_only, everything = _run(drive())

    assert "enrich 8.8.8.8" in session_only
    assert "someone else" not in session_only
    assert "someone else" in everything


def test_a_request_containing_markup_still_draws(tmp_path: Path) -> None:
    """A request the activity table cannot draw would close the interface.

    Bracketed paths are ordinary input for a security tool, and the request is
    replayed from the trace store on every start, so failing to render one
    keeps the interface shut until the trace ages out.
    """

    async def drive() -> str:
        app, _, _ = _build(tmp_path, request=BRACKETED_PATH)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            return _drawn(app, "#turns")

    assert "[/var/log/syslog]" in _run(drive())


def test_the_span_pane_follows_the_selected_run(tmp_path: Path) -> None:
    """A static detail pane beside a list of turns is a lie about what it shows."""

    async def drive() -> str:
        app, _, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            return _text(app, "#span-detail")

    detail = _run(drive())

    assert "enrich 8.8.8.8" in detail
    assert "LangGraph" in detail
    assert "ChatOpenAI" in detail


def test_without_traces_the_activity_tab_explains_itself(tmp_path: Path) -> None:
    """Tracing is optional, so an empty pane has to say why it is empty."""

    async def drive() -> tuple[str, str]:
        app, _, _ = _build(tmp_path, traces=False)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            summary = str(app.query_one("#summary").render())
            await pilot.press("f1")
            await pilot.pause()
            await _ask(app, pilot, "hello")
            return summary, _text(app, "#conversation")

    summary, conversation = _run(drive())

    assert "start-phoenix.sh" in summary
    assert "Censys reports" in conversation


def test_evidence_opens_from_the_selected_run_without_typing_an_id(
    tmp_path: Path,
) -> None:
    """Typing a six-character ID is a CLI limitation, not a thing to reproduce."""

    async def drive() -> tuple[type, str]:
        app, _, _ = _build(tmp_path, evidence=True)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            return type(app.screen), _text(app, "#viewer")

    screen, viewer = _run(drive())

    assert screen is EvidenceScreen
    assert "censys" in viewer


def test_evidence_written_a_moment_into_a_run_still_attaches_to_it(
    tmp_path: Path,
) -> None:
    """A report's filename keeps whole seconds; a run's start does not.

    So a report written a fraction of a second in dates from just before the
    run began. Rejecting it offers no evidence for a run that stored some, and
    a missing key is indistinguishable from a run that collected nothing.
    """

    async def drive() -> tuple[dict[str, str], str]:
        # A run that began a tenth of a second past the tick, which is what an
        # ordinary one does.
        started_at = STARTED_AT + timedelta(milliseconds=100)
        app, _, _ = _build(tmp_path, evidence=False, started_at=started_at)
        # Written 0.8s into that run, so the filename records the second before
        # it started and the offset comes out at −0.1s.
        stored = app.threat_report_store.save(
            "enrich 8.8.8.8",
            {"enrich": {}},
            thread_id=THREAD_ID,
            now=started_at + timedelta(milliseconds=800),
        )
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            return dict(app._evidence_ids), stored.report_id

    matched, report_id = _run(drive())

    # The link itself, not what pressing `e` shows: with nothing matched the
    # key falls back to the newest report and hides the failure.
    assert matched == {"aaa": report_id}


def test_evidence_from_another_conversation_never_attaches(tmp_path: Path) -> None:
    """Two conversations running seconds apart would otherwise cross over.

    The activity tab can show every session at once, and the time window alone
    cannot tell two overlapping runs apart. Offering one conversation's
    provider evidence against another's run is the wrong answer, not a
    cosmetic one.
    """

    async def drive() -> dict[str, str]:
        app, _, _ = _build(tmp_path, evidence=False)
        app.threat_report_store.save(
            "enrich 8.8.8.8",
            {"enrich": {}},
            thread_id="a-different-conversation",
            now=STARTED_AT + timedelta(seconds=2),
        )
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            return dict(app._evidence_ids)

    assert _run(drive()) == {}


def test_prompts_open_for_the_selected_run(tmp_path: Path) -> None:
    """Which prompt produced this answer is a question about a specific run."""

    async def drive() -> tuple[type, str]:
        app, _, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            return type(app.screen), _text(app, "#viewer")

    screen, viewer = _run(drive())

    assert screen is PromptScreen
    assert "Threat Intel specialist" in viewer


def test_single_letter_bindings_do_not_fire_while_typing(tmp_path: Path) -> None:
    """A chat box that opens a modal on the letter 'e' cannot be typed in."""

    async def drive() -> tuple[str, type]:
        app, _, _ = _build(tmp_path, evidence=True)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press(*"experiment")
            await pilot.pause()
            return app.query_one("#prompt").value, type(app.screen)

    typed, screen = _run(drive())

    assert typed == "experiment"
    assert screen is not EvidenceScreen


def test_activity_exports_to_json(tmp_path: Path) -> None:
    """Evidence that leaves the terminal is what a ticket or a spreadsheet needs."""

    async def drive() -> None:
        app, _, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()

    _run(drive())

    exported = list((tmp_path / "exports").glob("activity-*.json"))
    assert len(exported) == 1
    assert "enrich 8.8.8.8" in exported[0].read_text(encoding="utf-8")


def test_choosing_a_session_switches_the_conversation_and_persists(
    tmp_path: Path,
) -> None:
    """Picking a conversation is a decision that has to outlive the process."""
    checkpoint_database_path = tmp_path / "checkpoints.sqlite"
    write_session(checkpoint_database_path, "older", [("what is a canary token", "…")])

    async def drive() -> tuple[str, str]:
        app, _, _ = _build(tmp_path)
        app.checkpoint_database_path = checkpoint_database_path
        async with app.run_test(size=(110, 30)) as pilot:
            sessions = app.query_one("#sessions", ListView)
            sessions.focus()
            sessions.index = sessions.index or 0
            while app._session_ids[sessions.index] != "older":
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            return app.thread_id, _text(app, "#conversation")

    thread_id, conversation = _run(drive())

    assert thread_id == "older"
    assert "canary token" in conversation
    assert (tmp_path / "current_session").read_text(encoding="utf-8").strip() == "older"


def test_evidence_has_one_home(tmp_path: Path) -> None:
    """Two ways into the same viewer is two places to keep working; keep one."""

    async def drive() -> tuple[type, bool, type, str]:
        app, _, _ = _build(tmp_path, evidence=True)
        async with app.run_test(size=(110, 30)) as pilot:
            # On the chat tab the key is inert, and the footer does not offer it.
            app.query_one("#sessions", ListView).focus()
            await pilot.press("e")
            await pilot.pause()
            from_chat = type(app.screen)
            offered = app.check_action("open_evidence", ())

            # `/threat show` still works, but it answers on the activity tab.
            app.query_one("#prompt").focus()
            await pilot.press(*"/threat show", "enter")
            await pilot.pause()
            await pilot.pause()
            return (
                from_chat,
                bool(offered),
                type(app.screen),
                app.query_one(TabbedContent).active,
            )

    from_chat, offered, from_command, tab = _run(drive())

    assert from_chat is not EvidenceScreen
    assert offered is False
    assert from_command is EvidenceScreen
    assert tab == "activity"


def _with_sessions(tmp_path: Path) -> Path:
    checkpoint_database_path = tmp_path / "checkpoints.sqlite"
    write_session(checkpoint_database_path, THREAD_ID, [("enrich 8.8.8.8", "…")])
    write_session(checkpoint_database_path, "other", [("keep me", "…")])
    return checkpoint_database_path


def test_deleting_a_session_is_confirmed_first(tmp_path: Path) -> None:
    """One keystroke on a highlighted list is too cheap for something final."""

    async def drive() -> tuple[type, list[str], str]:
        app, _, _ = _build(tmp_path)
        app.checkpoint_database_path = _with_sessions(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            app._load_sessions()
            app.query_one("#sessions", ListView).focus()
            await pilot.press("d")
            await pilot.pause()
            asked = type(app.screen)
            await pilot.press("escape")
            await pilot.pause()
            return (
                asked,
                [s.thread_id for s in list_sessions(app.checkpoint_database_path)],
                type(app.screen).__name__,
            )

    asked, remaining, after = _run(drive())

    assert asked is ConfirmDeleteScreen
    # Cancelling leaves everything exactly as it was.
    assert sorted(remaining) == ["other", THREAD_ID]
    assert after == "Screen"


def test_confirming_removes_the_conversation_and_its_archived_answers(
    tmp_path: Path,
) -> None:
    """Deleting means gone: the thread and everything it put into /recall."""

    async def drive() -> tuple[list[str], list[str], str, str]:
        app, _, knowledge = _build(tmp_path)
        app.checkpoint_database_path = _with_sessions(tmp_path)
        knowledge.documents = [
            SimpleNamespace(source_ref=THREAD_ID),
            SimpleNamespace(source_ref="other"),
        ]
        async with app.run_test(size=(110, 30)) as pilot:
            app._load_sessions()
            app.query_one("#sessions", ListView).focus()
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            return (
                [s.thread_id for s in list_sessions(app.checkpoint_database_path)],
                [d.source_ref for d in knowledge.documents],
                app.thread_id,
                _text(app, "#conversation"),
            )

    remaining, archived, thread_id, conversation = _run(drive())

    assert remaining == ["other"]
    assert archived == ["other"]
    # Deleting the conversation being continued leaves a fresh one, not a gap.
    assert thread_id != THREAD_ID
    assert "New session" in conversation


def test_confirming_also_removes_the_evidence_that_conversation_collected(
    tmp_path: Path,
) -> None:
    """Evidence is the most sensitive thing a deletion could leave behind.

    Each report holds every indicator investigated and everything the providers
    returned about it, kept for a month. Deleting the conversation that asked
    for them has to take them too, has to say so before it does, and must not
    reach into another conversation's.
    """

    async def drive() -> tuple[str, list[str], list[str | None]]:
        app, _, _ = _build(tmp_path, evidence=True)
        app.checkpoint_database_path = _with_sessions(tmp_path)
        app.threat_report_store.save(
            "enrich 1.1.1.1", {"enrich": {}}, thread_id="other"
        )
        async with app.run_test(size=(110, 30)) as pilot:
            app._load_sessions()
            app.query_one("#sessions", ListView).focus()
            await pilot.press("d")
            await pilot.pause()
            warned = _drawn(app, "#confirm")
            await pilot.press("d")
            await pilot.pause()
            return (
                warned,
                [s.thread_id for s in list_sessions(app.checkpoint_database_path)],
                [report.thread_id for report in app.threat_report_store.recent()],
            )

    warned, remaining, reports = _run(drive())

    assert "1 evidence reports" in warned
    assert remaining == ["other"]
    assert reports == ["other"]


def test_deleting_is_offered_on_the_chat_tab_only(tmp_path: Path) -> None:
    """Sessions are listed on one tab, so they are deleted from one tab."""

    async def drive() -> tuple[bool, bool]:
        app, _, _ = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            on_chat = app.check_action("delete_session", ())
            await pilot.press("f2")
            await pilot.pause()
            return bool(on_chat), bool(app.check_action("delete_session", ()))

    on_chat, on_activity = _run(drive())

    assert on_chat is True
    assert on_activity is False
