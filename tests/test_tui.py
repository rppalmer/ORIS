"""Smoke test for the placeholder terminal interface.

Skips entirely when the optional `tui` extra is not installed, so the normal
install and its test run are unaffected by a spike that may be deleted.
"""

import asyncio

import pytest

pytest.importorskip("textual", reason="install the optional 'tui' extra")

from oris.tui import OrisTui  # noqa: E402


def test_tui_mounts_and_switches_tabs() -> None:
    """The shell exists to be looked at, so the only contract is that it renders."""

    async def drive() -> tuple[str, str]:
        app = OrisTui()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            first = app.query_one("TabbedContent").active
            await pilot.press("2")
            await pilot.pause()
            return first, app.query_one("TabbedContent").active

    opening, after = asyncio.run(drive())

    assert opening == "chat"
    assert after == "activity"


def test_tui_imports_nothing_from_the_rest_of_oris() -> None:
    """Its value as a spike is that deleting it can change nothing else.

    The moment it imports application code, removing it stops being free and
    something else starts depending on it.
    """
    import inspect

    import oris.tui

    source = inspect.getsource(oris.tui)
    offending = [
        line
        for line in source.splitlines()
        if line.startswith(("import oris", "from oris"))
    ]

    assert offending == []


def test_span_pane_follows_the_selected_turn() -> None:
    """A static detail pane beside a list of turns is a lie about what it shows."""

    async def drive() -> tuple[str, str]:
        app = OrisTui()
        async with app.run_test(size=(110, 26)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            table = app.query_one("#turns")
            first = app.query_one("#span-detail").lines[0].text
            table.move_cursor(row=3)
            await pilot.pause()
            return first, app.query_one("#span-detail").lines[0].text

    opening, after = asyncio.run(drive())

    assert "/threat enrich" in opening
    assert "/community" in after


def test_evidence_opens_from_the_selected_turn_without_typing_an_id() -> None:
    """Typing a six-character ID is a CLI limitation, not a thing to reproduce."""

    async def drive() -> str:
        app = OrisTui()
        async with app.run_test(size=(110, 26)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            return app.screen.__class__.__name__

    assert asyncio.run(drive()) == "EvidenceScreen"
