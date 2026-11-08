"""The ORIS web site, served by ORIS itself to the local network.

There is no login. The network it listens on is trusted, and authentication is
a later feature, so anyone who can reach the port can use the chat.

The site is being built one feature at a time. Chat is plain chat with the
model, through the same graph the other interfaces use, so the system prompt,
history limits, tracing and saved conversations all come from there. Tools looks
up an indicator, CVE or threat actor with ThreatSyft and shows what came back,
with no model involved.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import uvicorn
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from markdown_it import MarkdownIt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from oris.config import load_settings
from oris.sessions import session_transcript
from oris.threat_intel import call_tool

HOST = "0.0.0.0"
PORT = 8420
CHAT_PAGE = Path(__file__).with_name("web_chat.html")
TOOLS_PAGE = Path(__file__).with_name("web_tools.html")
STYLES = Path(__file__).with_name("web.css")
LOOKUP_TOOLS = frozenset({"enrich", "lookup"})
"""The ThreatSyft tools the Tools page offers, and the only ones it can reach."""

# Answers can carry text from the open web, and the page inserts this output as
# HTML, so raw HTML stays off. Images stay off as well: a browser fetches one the
# moment it appears, so an address planted by a web page could carry text from
# the conversation to whoever runs that server. They are shown as links instead.
_markdown = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")


def _message(role: str, text: str) -> dict[str, str]:
    """One saved message, as the page shows it."""
    if role == "you":
        return {"role": role, "text": text}
    return {"role": role, "html": _markdown.render(text)}


def create_app(
    graph: CompiledStateGraph,
    checkpoint_database_path: Path,
    load_threatsyft: Callable[[], Awaitable[dict[str, BaseTool]]],
) -> Starlette:
    """Build the site around one compiled graph and its conversation store.

    ThreatSyft is loaded on the first lookup rather than here, so the site still
    starts and chats on a machine where ThreatSyft is missing or broken.
    """
    threatsyft: dict[str, BaseTool] | None = None

    def new_conversation(_request: Request) -> RedirectResponse:
        # The server picks the ID because browsers only offer random UUIDs on
        # HTTPS or localhost, and other devices reach this over plain HTTP.
        return RedirectResponse(f"/chat/{uuid4()}", status_code=303)

    def chat_page(_request: Request) -> FileResponse:
        return FileResponse(CHAT_PAGE, media_type="text/html")

    def conversation(request: Request) -> JSONResponse:
        transcript = session_transcript(
            checkpoint_database_path,
            str(request.path_params["conversation_id"]),
        )
        return JSONResponse(
            {"messages": [_message(role, text) for role, text in transcript]}
        )

    async def send_message(request: Request) -> JSONResponse:
        text = (await request.json())["text"]
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=text)], "mode": "chat"},
            {
                "configurable": {
                    "thread_id": str(request.path_params["conversation_id"])
                }
            },
        )
        if not result["request_succeeded"]:
            return JSONResponse({"error": result["request_error"]}, status_code=502)
        return JSONResponse(
            {"html": _markdown.render(str(result["messages"][-1].text))}
        )

    def tools_page(_request: Request) -> FileResponse:
        return FileResponse(TOOLS_PAGE, media_type="text/html")

    def styles(_request: Request) -> FileResponse:
        return FileResponse(STYLES, media_type="text/css")

    async def look_up(request: Request) -> JSONResponse:
        nonlocal threatsyft
        name = request.path_params["tool"]
        if name not in LOOKUP_TOOLS:
            return JSONResponse({"error": f"No tool named {name}."}, status_code=404)
        try:
            if threatsyft is None:
                threatsyft = await load_threatsyft()
            return JSONResponse(await call_tool(threatsyft[name], await request.json()))
        except Exception as error:  # noqa: BLE001 - a failed lookup is not a crash
            return JSONResponse(
                {"error": f"ThreatSyft could not run the lookup: {error}"},
                status_code=502,
            )

    return Starlette(
        routes=[
            Route("/", new_conversation),
            Route("/web.css", styles),
            Route("/tools", tools_page),
            Route("/api/tools/{tool}", look_up, methods=["POST"]),
            Route("/chat/{conversation_id:uuid}", chat_page),
            Route("/api/chat/{conversation_id:uuid}", conversation),
            Route(
                "/api/chat/{conversation_id:uuid}",
                send_message,
                methods=["POST"],
            ),
        ]
    )


async def _serve() -> None:
    """Compile the graph and serve the site until stopped."""
    settings = load_settings()
    database_path = settings.checkpoint_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)

    # Imported here because importing it loads the settings and builds the
    # model, which should not happen merely by importing this module.
    from oris.web_research_app import build_oris_graph, load_threatsyft_by_name

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        graph = await build_oris_graph(checkpointer)
        app = create_app(graph, database_path, load_threatsyft_by_name)
        await uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT)).serve()


def main() -> None:
    """Start the ORIS web site."""
    asyncio.run(_serve())
