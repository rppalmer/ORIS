import asyncio
from rich.text import Text
from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.events import MouseMove
from textual.widgets import Markdown as MarkdownView, Static


class Probe(App):
    CSS = """
    #conversation { height: 1fr; border: round $panel; padding: 0 1; }
    #conversation Static { height: auto; }
    #conversation MarkdownView { height: auto; margin: 0; }
    """

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="conversation")

    async def on_mount(self) -> None:
        c = self.query_one("#conversation", VerticalScroll)
        await c.mount(Static(Text.assemble(("> ", "bold cyan"), ("what is [brackets]", "bold"))))
        await c.mount(MarkdownView("An **answer** with a `code` bit and a list:\n\n- one\n- two"))
        table = Table(show_header=False, box=None)
        table.add_column(); table.add_column()
        table.add_row(Text("/podcasts [show]"), Text("Catch up."))
        await c.mount(Static(table))


async def drag(pilot, widget, start, end):
    await pilot.mouse_down(widget, start)
    await pilot._post_mouse_events([MouseMove], widget=widget, offset=end, button=1)
    await pilot.mouse_up(widget, end)
    await pilot.pause()


async def main():
    app = Probe()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        c = app.query_one("#conversation", VerticalScroll)
        print("children:", [type(w).__name__ for w in c.children])
        md = c.query_one(MarkdownView)
        st = c.children[0]
        print("--- rendered screen ---")
        print("\n".join(l.text.rstrip() for l in app.screen._compositor.render_strips()[:12]))
        await drag(pilot, st, (2, 0), (16, 0))
        print("request line selection:", repr(app.screen.get_selected_text()))
        await drag(pilot, md, (0, 0), (14, 0))
        print("answer selection      :", repr(app.screen.get_selected_text()))

asyncio.run(main())
