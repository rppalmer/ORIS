"""The ORIS web site, served by ORIS itself to the local network.

There is no login. The network it listens on is trusted, and authentication is
a later feature, so anyone who can reach the port can use the chat.

The site is being built one feature at a time. Chat is plain chat with the
model, through the same graph the other interfaces use, so the system prompt,
history limits, tracing and saved conversations all come from there. Tools looks
up an indicator, CVE or threat actor with ThreatSyft and shows what came back,
with no model involved. Status says whether ThreatSyft is ready to do that. Runs
shows the scheduled jobs, what they did, and the reports they wrote. Search looks
through Hacker News, X, arXiv and GitHub with Net-Razor, also with no model.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

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
from oris.net_razor import call_net_razor_tool
from oris.scheduled_run_history import DEFAULT_ROOT, ScheduledRun, ScheduledRunHistory
from oris.schedules import (
    DEFAULT_SCHEDULE_FILE,
    job_next_run,
    load_schedule_config,
)
from oris.sessions import session_transcript
from oris.threat_intel import call_tool

HOST = "0.0.0.0"
PORT = 8420
CHAT_PAGE = Path(__file__).with_name("web_chat.html")
TOOLS_PAGE = Path(__file__).with_name("web_tools.html")
STATUS_PAGE = Path(__file__).with_name("web_status.html")
RUNS_PAGE = Path(__file__).with_name("web_runs.html")
SEARCH_PAGE = Path(__file__).with_name("web_search.html")
STYLES = Path(__file__).with_name("web.css")
NAVIGATION = Path(__file__).with_name("web.js")
LOOKUP_TOOLS = frozenset({"enrich", "lookup"})
"""The ThreatSyft tools the Tools page offers, and the only ones it can reach."""
_RECORDED_RUNS = ScheduledRunHistory(DEFAULT_ROOT)
PAGE_SIZES = (20, 50, 100)
"""How many runs one page of the Runs list can show."""

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


def _file(path: Path, media_type: str) -> Callable[[Request], FileResponse]:
    """A route that serves one of the site's own files."""

    def serve(_request: Request) -> FileResponse:
        return FileResponse(path, media_type=media_type)

    return serve


def _threatsyft_failed(error: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": f"ThreatSyft could not answer: {error}"},
        status_code=502,
    )


def _schedule(path: Path) -> dict[str, Any]:
    """The configured jobs and when each next fires.

    A file that cannot be read is reported rather than raised, because the
    history of past runs is still worth showing without it.
    """
    try:
        config = load_schedule_config(path)
    except (OSError, ValueError) as error:
        return {"jobs": [], "schedule_error": f"Could not read {path}: {error}"}

    now = datetime.now(ZoneInfo(config.timezone))
    jobs = []
    for job in config.jobs:
        next_run = problem = None
        try:
            fires = job_next_run(job, config.timezone, after=now)
            next_run = fires.isoformat() if fires else None
        except ValueError as error:
            problem = str(error)
        jobs.append(
            {
                "id": job.id,
                "task": job.task,
                "cron": job.cron,
                "enabled": job.enabled,
                "next_run": next_run,
                "problem": problem,
            }
        )
    return {"jobs": jobs, "schedule_error": None}


def _whole_number(value: str | None, default: int) -> int:
    """Read a number from the address, which anyone can edit by hand."""
    try:
        number = int(value or "")
    except ValueError:
        return default
    return number if number >= 1 else default


def _run(run: ScheduledRun) -> dict[str, Any]:
    """One recorded run, as the page shows it."""
    return {
        "run_id": run.run_id,
        "short_id": run.short_id,
        "job_id": run.job_id,
        "task": run.task,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "duration_seconds": run.duration_seconds,
        "error": run.error,
        "has_report": run.has_report,
    }


def create_app(
    graph: CompiledStateGraph,
    checkpoint_database_path: Path,
    load_threatsyft: Callable[[], Awaitable[dict[str, BaseTool]]],
    load_search: Callable[[], Awaitable[BaseTool]],
    runs: ScheduledRunHistory = _RECORDED_RUNS,
    schedule_file: Path = DEFAULT_SCHEDULE_FILE,
) -> Starlette:
    """Build the site around one compiled graph and its conversation store.

    ThreatSyft and Net-Razor are loaded on first use rather than here, so the
    site still starts and chats on a machine where either is missing or broken.
    """
    threatsyft: dict[str, BaseTool] | None = None
    research: BaseTool | None = None

    def new_conversation(_request: Request) -> RedirectResponse:
        # The server picks the ID because browsers only offer random UUIDs on
        # HTTPS or localhost, and other devices reach this over plain HTTP.
        return RedirectResponse(f"/chat/{uuid4()}", status_code=303)

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

    async def connected() -> dict[str, BaseTool]:
        nonlocal threatsyft
        if threatsyft is None:
            threatsyft = await load_threatsyft()
        return threatsyft

    async def look_up(request: Request) -> JSONResponse:
        name = request.path_params["tool"]
        if name not in LOOKUP_TOOLS:
            return JSONResponse({"error": f"No tool named {name}."}, status_code=404)
        try:
            tool = (await connected())[name]
            return JSONResponse(await call_tool(tool, await request.json()))
        except Exception as error:  # noqa: BLE001 - a failed lookup is not a crash
            return _threatsyft_failed(error)

    async def status(_request: Request) -> JSONResponse:
        try:
            tools = await connected()
            enrichment, knowledge = await asyncio.gather(
                call_tool(tools["enrichment_status"], {}),
                call_tool(tools["knowledge_status"], {}),
            )
        except Exception as error:  # noqa: BLE001 - this page reports exactly this
            return _threatsyft_failed(error)
        return JSONResponse({"enrichment": enrichment, "knowledge": knowledge})

    def run_list(request: Request) -> JSONResponse:
        size = _whole_number(request.query_params.get("size"), PAGE_SIZES[0])
        if size not in PAGE_SIZES:
            size = PAGE_SIZES[0]
        page = _whole_number(request.query_params.get("page"), 1)
        job = request.query_params.get("job") or None
        listing = runs.recent(size, job_id=job, offset=(page - 1) * size)
        return JSONResponse(
            {
                **_schedule(schedule_file),
                "runs": [_run(run) for run in listing.runs],
                "total": listing.total,
                "page": page,
                "size": size,
                "job": job,
                "job_ids": list(runs.job_ids()),
                "page_sizes": list(PAGE_SIZES),
            }
        )

    async def search(request: Request) -> JSONResponse:
        nonlocal research
        try:
            if research is None:
                research = await load_search()
            body = await request.json()
            # Net-Razor checks these choices and reports each source's failure.
            return JSONResponse(
                await call_net_razor_tool(
                    research,
                    {
                        "topic": body["query"],
                        "sources": body["sources"],
                        "days": body["days"],
                        "max_results_per_source": body["limit"],
                    },
                )
            )
        except Exception as error:  # noqa: BLE001 - a failed search is not a crash
            return JSONResponse(
                {"error": f"Net-Razor could not answer: {error}"},
                status_code=502,
            )

    def run_detail(request: Request) -> JSONResponse:
        wanted = request.path_params["run_id"]
        # `find` matches on a prefix for people typing short IDs. A link always
        # carries the whole ID, so anything less is not this run.
        run = next((r for r in runs.find(wanted) if r.run_id == wanted), None)
        if run is None:
            return JSONResponse({"error": "No such run."}, status_code=404)
        report = runs.read_report(run)
        return JSONResponse(
            {
                "run": _run(run),
                "html": None if report is None else _markdown.render(report),
            }
        )

    return Starlette(
        routes=[
            Route("/", new_conversation),
            Route("/web.css", _file(STYLES, "text/css")),
            Route("/web.js", _file(NAVIGATION, "text/javascript")),
            Route("/tools", _file(TOOLS_PAGE, "text/html")),
            Route("/status", _file(STATUS_PAGE, "text/html")),
            Route("/api/status", status),
            Route("/runs", _file(RUNS_PAGE, "text/html")),
            Route("/runs/{run_id}", _file(RUNS_PAGE, "text/html")),
            Route("/api/runs", run_list),
            Route("/api/runs/{run_id}", run_detail),
            Route("/api/tools/{tool}", look_up, methods=["POST"]),
            Route("/search", _file(SEARCH_PAGE, "text/html")),
            Route("/api/search", search, methods=["POST"]),
            Route("/chat/{conversation_id:uuid}", _file(CHAT_PAGE, "text/html")),
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
    from oris.web_research_app import (
        build_oris_graph,
        load_research_tool,
        load_threatsyft_by_name,
    )

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        graph = await build_oris_graph(checkpointer)
        app = create_app(
            graph, database_path, load_threatsyft_by_name, load_research_tool
        )
        await uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT)).serve()


def main() -> None:
    """Start the ORIS web site."""
    asyncio.run(_serve())
