"""Opt-in end-to-end contract for the Community Research CLI path."""

import asyncio
import os

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from oris.cli import run_chat
from oris.knowledge import KnowledgeRepository

LIVE_COMMUNITY_CLI_ENABLED = os.environ.get("ORIS_RUN_LIVE_COMMUNITY_CLI_TESTS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_COMMUNITY_CLI_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_COMMUNITY_CLI_TESTS=1 to contact "
        "Net-Razor, X, Hacker News, and oMLX."
    ),
)
def test_community_command_persists_one_complete_live_turn(
    monkeypatch,
    capfd,
    tmp_path,
) -> None:
    """The real explicit CLI path completes, checkpoints, and indexes its answer."""
    responses = iter(["/community LangGraph", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    knowledge_repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    thread_id = "live-community-cli"

    async def run_live_cli() -> tuple[str, ...]:
        from oris.web_research_app import build_oris_graph

        async with AsyncSqliteSaver.from_conn_string(
            str(checkpoint_path)
        ) as checkpointer:
            graph = await build_oris_graph(checkpointer)
            await run_chat(
                graph,
                knowledge_repository,
                session_file_path=tmp_path / "current_session",
                thread_id=thread_id,
            )
            state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return tuple(message.content for message in state.values["messages"])

    messages = asyncio.run(run_live_cli())

    assert len(messages) == 2
    assert messages[0] == "LangGraph"
    assert "Community sources:" in messages[1]
    documents = knowledge_repository.search("LangGraph")
    assert len(documents) == 1
    assert documents[0].source_ref == thread_id
    assert messages[1] in documents[0].content
    assert "Community sources:" in capfd.readouterr().out
