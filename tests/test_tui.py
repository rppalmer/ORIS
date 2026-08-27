"""The terminal interface, driven through Textual's own test pilot.

Skips entirely when the optional `tui` extra is not installed, so an install
without it still has a clean test run.
"""

import asyncio
import html
import inspect
import logging
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from checkpoint_fixture import write_session
from langchain_core.messages import AIMessage
from phoenix_fixture import write_trace

pytest.importorskip("textual", reason="install the optional 'tui' extra")

from textual.events import MouseMove  # noqa: E402
from textual.geometry import Region  # noqa: E402
from textual.widgets import (  # noqa: E402
    DataTable,
    ListView,
    Markdown,
    Static,
    TabbedContent,
)

from oris.knowledge import KnowledgeRepository  # noqa: E402
from oris.sessions import list_sessions  # noqa: E402
from oris.threat_reports import ThreatReportStore  # noqa: E402
from oris.tui import (  # noqa: E402
    ConfirmDeleteScreen,
    EvidenceScreen,
    OrisTui,
    PromptScreen,
    quiet_background_logging,
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
    """Read back what a pane has actually rendered.

    Two shapes, because the two panes are built differently. The activity
    detail is still a log and holds its own rendered lines. The conversation is
    a container of one widget per message, which is what makes its text
    selectable, so it is read by rendering each leaf widget under it.
    """
    node = app.screen.query_one(selector)
    if hasattr(node, "lines"):
        return "\n".join(strip.text for strip in node.lines)
    return "\n".join(
        strip.text
        for widget in node.walk_children(with_self=False)
        if not widget.children
        for strip in widget.render_lines(
            Region(0, 0, widget.size.width, widget.size.height)
        )
    )


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

    assert "start Phoenix with s" in summary
    assert "Censys reports" in conversation


def test_the_activity_summary_counts_turns_beside_traced_runs(
    tmp_path: Path,
) -> None:
    """Runs and turns are different sets, and a reader assumes they are one.

    A failed run is removed from the conversation but keeps its trace; a turn
    taken while the collector was down stays in the conversation with no trace.
    A real session was found showing two hackback turns in the chat and one
    unrelated failed run in the activity pane, sharing nothing — each pane
    correct, and together looking like the wrong session was displayed.
    """

    checkpoint_database_path = tmp_path / "checkpoints.sqlite"
    # Two turns kept in the conversation against one traced run: the mismatch
    # this exists to surface.
    write_session(
        checkpoint_database_path,
        THREAD_ID,
        [("enrich 8.8.8.8", "…"), ("and the other one", "…")],
    )

    async def drive() -> str:
        app, _, _ = _build(tmp_path, traces=True)
        app.checkpoint_database_path = checkpoint_database_path
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            return str(app.query_one("#summary").render())

    summary = _run(drive())

    assert "2 turns" in summary
    assert "1 traced run" in summary
    # Counting one of anything must not read as a defect in the interface.
    assert "1 turns" not in summary
    assert "1 traced runs" not in summary


def test_a_narrow_session_says_how_much_it_is_hiding(tmp_path: Path) -> None:
    """One run out of many looks exactly like a store holding one run.

    The activity view is scoped to the current conversation on purpose, so
    older work sits under other threads and does not appear. Nothing said so
    while the table had rows in it — the hint existed only for the empty case,
    which is the one case where it was least needed.
    """

    async def drive() -> tuple[str, int, str, int]:
        app, _, _ = _build(tmp_path, traces=True)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            narrow = str(app.query_one("#summary").render())
            narrow_rows = app.query_one("#turns", DataTable).row_count
            await pilot.press("a")
            await pilot.pause()
            wide = str(app.query_one("#summary").render())
            wide_rows = app.query_one("#turns", DataTable).row_count
            return narrow, narrow_rows, wide, wide_rows

    narrow, narrow_rows, wide, wide_rows = _run(drive())

    assert wide_rows > narrow_rows
    assert f"{wide_rows - narrow_rows} more in other sessions" in narrow
    # The wide view is already showing everything, so it has nothing to offer.
    assert "more in other sessions" not in wide


def test_an_empty_session_is_not_confused_with_tracing_being_off(
    tmp_path: Path,
) -> None:
    """The two empty panes look identical and call for opposite responses.

    A store that has never recorded anything means the collector was never
    started. A store holding other sessions' runs but none of this one's means
    tracing worked and either this session is new or the collector has since
    stopped — which the age of the newest entry is what reveals. Telling the
    second reader to switch on a setting they already have on is how a real
    diagnosis got missed.
    """

    async def drive() -> str:
        app, _, _ = _build(tmp_path, traces=True)
        app.thread_id = "a-session-with-no-traces-of-its-own"
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            app.action_refresh_activity()
            await pilot.pause()
            return str(app.query_one("#summary").render())

    summary = _run(drive())

    assert "No traces for this session" in summary
    assert "ago" in summary
    # The advice for a store that has never recorded anything must not appear
    # here: this store plainly has.
    assert "start-phoenix.sh" not in summary


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


def test_a_run_that_stored_nothing_opens_nothing(tmp_path: Path) -> None:
    """A run with no evidence must not be handed somebody else's.

    Observed in real use: a Community Research search, which stores no evidence
    at all, opened the newest podcast catch-up in the store and presented it as
    that search's evidence. "No ID was typed" and "this run collected none" are
    different questions, and the newest-report fallback belongs only to the
    first. Showing the wrong run's data under the right run's name is worse than
    showing nothing.
    """

    async def drive() -> type:
        app, _, _ = _build(tmp_path, evidence=False)
        # A report exists and is the newest in the store, so the fallback has
        # something to wrongly return.
        app.threat_report_store.save(
            "podcasts Locked On Pistons",
            {"episodes": []},
            thread_id="a-different-conversation",
            now=STARTED_AT + timedelta(seconds=2),
        )
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            return type(app.screen)

    assert _run(drive()) is not EvidenceScreen


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


async def _drag(
    pilot: Any, widget: Any, start: tuple[int, int], end: tuple[int, int]
) -> None:
    """Select text the way a mouse does: press, move with the button down, release."""
    await pilot.mouse_down(widget, start)
    await pilot._post_mouse_events([MouseMove], widget=widget, offset=end, button=1)
    await pilot.mouse_up(widget, end)
    await pilot.pause()


def test_an_answer_can_be_selected_and_copied(tmp_path: Path) -> None:
    """Dragging across an answer yields exactly those characters.

    This is the whole reason the conversation is a container of message widgets
    rather than a log. A log accepted the drag, reported a selection, and handed
    back an empty string, so the interface looked like it supported copying and
    did not. The assertion is on the extracted text rather than on the widget
    type, because the widget is the means and the text is the requirement.
    """

    async def drive() -> tuple[str, list[str]]:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await _ask(app, pilot, "anything")
            answer = app.query_one("#conversation").query_one(Markdown)
            await _drag(pilot, answer, (0, 0), (13, 0))
            selected = app.screen.get_selected_text()
            app.screen.action_copy_text()
            return selected or "", list(copied)

    copied: list[str] = []
    with patch.object(
        OrisTui, "copy_to_clipboard", lambda _self, text: copied.append(text)
    ):
        selected, clipboard = _run(drive())

    # Deliberately not an exact column-to-character mapping. What has to hold is
    # that a drag extracts real characters from the answer and that copying
    # sends exactly those. Where column 13 lands depends on the widget's padding,
    # which is styling and is free to change.
    assert selected in ANSWER
    assert 0 < len(selected) < len(ANSWER)
    assert clipboard == [selected]


def test_a_request_can_be_selected_too(tmp_path: Path) -> None:
    """The question is as worth copying as the answer, and is a different widget."""

    async def drive() -> str:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await _ask(app, pilot, "enrich 8.8.8.8")
            asked = app.query_one("#conversation").query_one(".ask")
            await _drag(pilot, asked, (2, 0), (16, 0))
            return app.screen.get_selected_text() or ""

    assert _run(drive()) == "enrich 8.8.8.8"


def test_stored_evidence_can_be_selected_and_copied(tmp_path: Path) -> None:
    """Evidence exists to be taken somewhere else, so it has to come out.

    The viewer showed highlighted JSON through a widget that keeps no character
    positions, so a drag over it selected nothing at all. Highlighting to `Text`
    keeps the colours and makes the same characters extractable.
    """

    async def drive() -> str:
        app, _graph, _knowledge = _build(tmp_path, evidence=True)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            body = app.screen.query_one("#viewer").query_one(Static)
            await _drag(pilot, body, (0, 2), (60, 6))
            return app.screen.get_selected_text() or ""

    selected = _run(drive())

    assert "censys" in selected


def test_the_span_detail_pane_can_be_selected(tmp_path: Path) -> None:
    """A span name and its timing are what gets pasted into a note about a slow run."""

    async def drive() -> str:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            body = app.screen.query_one("#span-detail").query_one(Static)
            await _drag(pilot, body, (0, 0), (40, 3))
            return app.screen.get_selected_text() or ""

    assert "enrich 8.8.8.8" in _run(drive())


def _phoenix(tmp_path: Path, *, installed: bool = True) -> SimpleNamespace:
    """A Phoenix service description pointing at a plist under tmp_path."""
    plist = tmp_path / "com.rppalmer.oris.phoenix.plist"
    if installed:
        plist.write_text("<plist/>")
    return SimpleNamespace(label="com.rppalmer.oris.phoenix", installed=plist)


def test_the_activity_tab_says_whether_phoenix_is_running(tmp_path: Path) -> None:
    """An empty activity view has two very different causes.

    Traces can be absent because nothing ran, or because the collector is down.
    Those look identical in the table and call for opposite responses, so the
    service state is shown beside it.
    """

    async def drive() -> str:
        app, _graph, _knowledge = _build(tmp_path, traces=False)
        app.phoenix_paths = _phoenix(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            return str(app.query_one("#services").render())

    with patch("oris.tui.is_loaded", return_value=True):
        assert "running" in _run(drive())

    with patch("oris.tui.is_loaded", return_value=False):
        assert "stopped" in _run(drive())


def test_a_service_that_was_never_installed_is_not_called_stopped(
    tmp_path: Path,
) -> None:
    """The two need different cures, so naming them the same misdirects.

    A stopped service starts with one key. One that was never installed cannot,
    and looking for why it will not start is looking for the wrong problem.
    """

    async def drive() -> tuple[str, list[str]]:
        app, _graph, _knowledge = _build(tmp_path)
        app.phoenix_paths = _phoenix(tmp_path, installed=False)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            shown = str(app.query_one("#services").render())
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return shown, started

    started: list[str] = []
    with patch("oris.tui.start_service", side_effect=lambda p: started.append("x")):
        shown, attempted = _run(drive())

    assert "not installed" in shown
    assert attempted == []


@pytest.mark.parametrize(
    ("running", "expected"),
    [(False, ["start"]), (True, ["stop"])],
)
def test_phoenix_starts_and_stops_from_the_activity_tab(
    tmp_path: Path,
    running: bool,
    expected: list[str],
) -> None:
    """One key, and which way it goes is read from launchd rather than remembered.

    The service can also be started or stopped outside this interface, so a
    toggle that trusted its own last action would send the opposite command.
    """
    called: list[str] = []

    async def drive() -> list[str]:
        app, _graph, _knowledge = _build(tmp_path)
        app.phoenix_paths = _phoenix(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()
        return called

    with (
        patch("oris.tui.is_loaded", return_value=running),
        patch("oris.tui.start_service", side_effect=lambda p: called.append("start")),
        patch("oris.tui.stop_service", side_effect=lambda p: called.append("stop")),
    ):
        assert _run(drive()) == expected


def test_a_service_that_will_not_start_does_not_take_the_interface_down(
    tmp_path: Path,
) -> None:
    """Tracing is optional. A dead collector must never stop the chat working."""

    async def drive() -> str:
        app, _graph, _knowledge = _build(tmp_path)
        app.phoenix_paths = _phoenix(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("f1")
            await pilot.pause()
            await _ask(app, pilot, "still working?")
            return _text(app, "#conversation")

    def explode(_paths: Any) -> None:
        raise OSError("launchctl bootstrap failed")

    with (
        patch("oris.tui.is_loaded", return_value=False),
        patch("oris.tui.start_service", side_effect=explode),
    ):
        conversation = _run(drive())

    assert ANSWER in conversation


def test_the_phoenix_keys_are_not_offered_on_the_chat_tab(tmp_path: Path) -> None:
    """Single letters belong to the chat box while it is on screen."""

    async def drive() -> tuple[bool, bool]:
        app, _graph, _knowledge = _build(tmp_path)
        app.phoenix_paths = _phoenix(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            on_chat = app.check_action("phoenix_toggle", ())
            await pilot.press("f2")
            await pilot.pause()
            return bool(on_chat), bool(app.check_action("phoenix_toggle", ()))

    on_chat, on_activity = _run(drive())

    assert not on_chat
    assert on_activity


def test_a_subprocess_logs_to_a_file_rather_than_over_the_interface(
    tmp_path: Path,
) -> None:
    """A stdio MCP server writes its own logging to this process's stderr.

    Net-Razor logs a JSON line per request. Landing on the terminal Textual is
    drawing, those lines scroll the frame out from under the interface.

    Driven with a real child process handed the stream the MCP client would
    hand it, because that is the thing that has to end up somewhere else.
    """
    log = tmp_path / "logs" / "mcp-servers.log"
    noisy = [
        sys.executable,
        "-c",
        "import sys; print('server noise', file=sys.stderr)",
    ]

    with quiet_background_logging(log):
        subprocess.run(noisy, check=True, stderr=sys.stderr)

    assert "server noise" in log.read_text()


def test_the_interface_itself_is_not_redirected(tmp_path: Path) -> None:
    """The bug this replaced: the whole interface drew into the log file.

    Textual writes every frame to `sys.__stderr__`. Redirecting one level lower,
    at the file descriptor, moved that too — so `oris-tui` painted itself into
    `~/.oris/logs` and the terminal stayed blank. Over SSH it looked like the
    command did nothing at all.

    Two names for one stream is what makes the separation possible, so both
    halves are asserted: the servers' name moves, the interface's does not.
    """
    log = tmp_path / "logs" / "mcp-servers.log"
    drawing_surface = sys.__stderr__

    with quiet_background_logging(log):
        assert sys.stderr is not drawing_surface
        assert sys.__stderr__ is drawing_surface

    assert sys.__stderr__ is drawing_surface


def test_textual_still_draws_on_the_stream_this_leaves_alone() -> None:
    """Pins the upstream fact the separation depends on.

    If Textual ever draws on `sys.stderr` instead, the redirection would hide
    the interface again. That should fail here rather than on someone's screen.
    """
    from textual.drivers.linux_driver import LinuxDriver

    source = inspect.getsource(LinuxDriver.__init__)

    assert "sys.__stderr__" in source


def test_the_interface_module_does_not_load_the_mcp_client() -> None:
    """The redirection only works if the MCP client is imported after it.

    The client decides where a server's logging goes at import time, by taking
    `sys.stderr` as a default argument value. If importing this module pulled
    the client in, that decision would already be made — against the terminal —
    before the interface ever started.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, oris.tui; "
            "print(any(m.startswith('mcp') for m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"


def test_the_stream_is_restored_afterwards(tmp_path: Path) -> None:
    """Once the interface exits, a crash still reports itself to the terminal."""
    log = tmp_path / "logs" / "mcp-servers.log"
    before = sys.stderr

    with quiet_background_logging(log):
        print("while running", file=sys.stderr)
    print("after exiting", file=sys.stderr)

    assert sys.stderr is before
    assert "while running" in log.read_text()
    assert "after exiting" not in log.read_text()


def test_every_framed_pane_is_drawn_with_the_same_border(tmp_path: Path) -> None:
    """One style across the interface, not one per widget's default.

    The input box kept Textual's default `tall` border: thick half-blocks down
    the sides, thin lines along the top and bottom, and no corners joining
    them. Beside three `round` panels it read as a broken box rather than as a
    style. Asserting they agree, rather than asserting one value, is what would
    catch a new pane arriving with its own default.
    """

    async def drive() -> set[str]:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            return {
                edge[0]
                for selector in ("#sessions", "#conversation", "#prompt")
                for edge in (
                    app.query_one(selector).styles.border_top,
                    app.query_one(selector).styles.border_bottom,
                    app.query_one(selector).styles.border_left,
                    app.query_one(selector).styles.border_right,
                )
            }

    assert _run(drive()) == {"round"}


def test_copying_says_what_happened(tmp_path: Path) -> None:
    """A copy that worked and one that found nothing looked identical.

    Textual's own binding is silent either way, and the clipboard itself is a
    terminal setting ORIS cannot see. With no feedback there is no way to tell
    a failed copy from a terminal that refused it, which is most of the trouble
    with diagnosing one.
    """

    async def drive() -> tuple[list[str], list[str]]:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await _ask(app, pilot, "anything")
            with patch.object(type(app), "notify", lambda s, m, **k: said.append(m)):
                await pilot.press("ctrl+c")
                await pilot.pause()
                nothing_selected = list(said)
                said.clear()

                answer = app.query_one("#conversation").query_one(Markdown)
                await _drag(pilot, answer, (0, 0), (13, 0))
                await pilot.press("ctrl+c")
                await pilot.pause()
                return nothing_selected, list(said)

    said: list[str] = []
    with patch.object(OrisTui, "copy_to_clipboard", lambda s, t: None):
        empty, copied = _run(drive())

    assert any("Nothing selected" in message for message in empty)
    assert any("Copied" in message for message in copied)
    # The terminal is the half ORIS cannot verify, so the message says so
    # rather than reporting a success that may not have reached a clipboard.
    assert any("terminal is blocking" in message for message in copied)


def test_the_trace_exporter_is_quiet_even_at_shutdown(tmp_path: Path) -> None:
    """Its noisiest moment is after the interface has already exited.

    A batch of spans is flushed at interpreter shutdown. With no collector
    running that flush retries and complains, and by then the redirection is
    undone, so the complaints land on the terminal the user is looking at —
    which is what happened on the Mac mini after a /podcasts run.

    Rebinding `sys.stderr` cannot cover that, because it is deliberately put
    back on the way out. The exporter's own logger has to be pointed at the
    file and kept there.
    """
    log = tmp_path / "logs" / "mcp-servers.log"
    exporter = logging.getLogger("opentelemetry")
    saved_handlers, saved_propagate = exporter.handlers[:], exporter.propagate

    try:
        with quiet_background_logging(log):
            exporter.warning("during the run")
        exporter.warning("after the interface exited")
    finally:
        exporter.handlers, exporter.propagate = saved_handlers, saved_propagate

    written = log.read_text()

    assert "during the run" in written
    assert "after the interface exited" in written


def test_a_transcript_is_shown_as_prose_not_as_escaped_json() -> None:
    """The same key opens both kinds of evidence, in the form each is readable in.

    Rendered as JSON a transcript is one enormous line with every newline
    written out as a backslash-n — the same evidence, and unreadable. The shape
    of what was stored decides the rendering.
    """
    screen = EvidenceScreen(
        {
            "report_id": "a1b2c3",
            "request": "podcasts LINUX Unplugged",
            "evidence": {
                "episodes": [
                    {
                        "show": "LINUX Unplugged",
                        "title": "Episode 600",
                        "url": "https://example.com/600",
                        "transcript_backend": "whisper",
                        "transcript_truncated": False,
                        "transcript": "First line.\nSecond line.",
                    }
                ]
            },
        }
    )

    rendered = str(screen.body())

    assert "First line.\nSecond line." in rendered
    assert "\\n" not in rendered
    # The backend belongs here, not only in the digest: this is where someone
    # comes to check a name the summary got wrong, and machine transcription is
    # the first thing that explains one.
    assert "whisper" in rendered
    assert "LINUX Unplugged — Episode 600" in rendered


def test_provider_evidence_is_still_shown_as_json() -> None:
    """Threat Intel evidence is JSON and reads best as JSON; nothing changed."""
    screen = EvidenceScreen(
        {"report_id": "a1b2c3", "evidence": {"enrich": {"censys": {"ok": True}}}}
    )

    rendered = str(screen.body())

    assert '"censys"' in rendered
    assert '"ok": true' in rendered


def test_pressing_e_on_a_podcast_run_opens_its_transcript(tmp_path: Path) -> None:
    """The point of storing it: one key, whichever specialist produced the run.

    Storing the transcript and rendering it are each tested on their own; this
    is the path between them, which is the part that has to find the right run.
    """

    async def drive() -> tuple[type, str]:
        app, _graph, _knowledge = _build(tmp_path, request="podcasts")
        app.threat_report_store.save(
            "podcasts LINUX Unplugged",
            {
                "episodes": [
                    {
                        "show": "LINUX Unplugged",
                        "title": "Episode 600",
                        "url": "https://example.com/600",
                        "transcript_backend": "publisher",
                        "transcript_truncated": False,
                        "transcript": "Wes said the release slipped.",
                    }
                ]
            },
            thread_id=THREAD_ID,
            now=STARTED_AT.replace(second=31),
        )
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            return type(app.screen), _text(app, "#viewer")

    screen, viewer = _run(drive())

    assert screen is EvidenceScreen
    assert "Wes said the release slipped." in viewer


@pytest.mark.parametrize("by_key", [True, False])
def test_a_new_session_can_be_started_without_typing(
    tmp_path: Path,
    by_key: bool,
) -> None:
    """`ctrl+n` and `/new` are the same thing, reached two ways.

    A chord rather than a bare letter because the input box holds focus almost
    the whole time in chat and swallows printable keys, so a single letter would
    only work after tabbing away from where you are typing.
    """

    async def drive() -> tuple[str, str]:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "first question")
            before = app.thread_id
            if by_key:
                await pilot.press("ctrl+n")
                await pilot.pause()
            else:
                await _ask(app, pilot, "/new")
            return before, app.thread_id

    before, after = _run(drive())

    assert after != before


def test_the_new_session_key_clears_the_conversation(tmp_path: Path) -> None:
    """A fresh session shows the empty-session line, not the previous answer."""

    async def drive() -> str:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await _ask(app, pilot, "first question")
            assert ANSWER in _text(app, "#conversation")
            await pilot.press("ctrl+n")
            await pilot.pause()
            return _text(app, "#conversation")

    conversation = _run(drive())

    assert ANSWER not in conversation
    assert "New session" in conversation


def test_a_failure_after_the_answer_does_not_close_the_interface(
    tmp_path: Path,
) -> None:
    """Only the call to the graph used to be guarded.

    Textual closes the app when a worker raises, so a failure while showing the
    answer, archiving it, or refreshing the activity view took the interface
    down mid-question — and with stderr going to a log file there was nothing
    on screen to say why. Losing the conversation is never the right response
    to a failed turn.
    """

    async def drive() -> tuple[bool, str]:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            with patch.object(
                type(app.knowledge_repository),
                "add_exchange",
                side_effect=OSError("archive is read-only"),
            ):
                await _ask(app, pilot, "anything")
            return app.is_running, _text(app, "#conversation")

    still_running, conversation = _run(drive())

    assert still_running
    assert "archive is read-only" in conversation
    # The answer was shown before the archive was written, so it survives the
    # archive failing -- which is the reason that ordering exists.
    assert ANSWER in conversation


def test_the_prompt_comes_back_after_a_failed_turn(tmp_path: Path) -> None:
    """A disabled prompt with no error on screen looks like a frozen interface."""

    async def drive() -> bool:
        app, _graph, _knowledge = _build(tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            with patch.object(
                type(app.knowledge_repository),
                "add_exchange",
                side_effect=OSError("archive is read-only"),
            ):
                await _ask(app, pilot, "anything")
            return app.query_one("#prompt").disabled

    assert _run(drive()) is False
