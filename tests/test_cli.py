"""Tests for the ORIS command-line interface."""

import asyncio
import readline
from unittest.mock import Mock

from langchain_core.messages import AIMessage
from rich.console import Console

from oris.cli import cli_history, print_banner, run_chat
from oris.knowledge import KnowledgeDocument, KnowledgeRepository


def streaming_graph(result: dict | None = None, steps: tuple[str, ...] = ()) -> Mock:
    """A graph that streams named steps and ends with one result.

    The interface streams rather than invokes so it can say which step is
    running, so a fake has to be an async iterator rather than a coroutine.
    `calls` records what the graph was asked for, which is what the tests
    about routing actually assert on.
    """
    graph = Mock()
    graph.calls = Mock()

    async def astream(request, config=None, /, **kwargs):
        graph.calls(request, config, **kwargs)
        for name in steps:
            yield ("", "debug", {"type": "task", "payload": {"name": name}})
        yield ((), "values", result if result is not None else {})

    graph.astream = astream
    return graph


def test_run_chat_prints_error_text_containing_console_markup(
    monkeypatch, capsys, tmp_path
) -> None:
    """Runtime text is never parsed as console markup, whatever it contains."""
    graph = streaming_graph(
        {
            "request_succeeded": False,
            "request_error": "Web Research failed: tool returned [/] and [bold]",
        }
    )
    responses = iter(["/research anything", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            Mock(),
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    printed = capsys.readouterr().out
    assert "tool returned [/] and [bold]" in printed


def test_run_chat_reports_an_unknown_command_containing_markup(
    monkeypatch, capsys, tmp_path
) -> None:
    """Whatever the user types is echoed back safely, not parsed as markup."""
    graph = streaming_graph()
    responses = iter(["/[/]", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            Mock(),
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    assert "Unknown command: /[/]" in capsys.readouterr().out
    graph.calls.assert_not_called()


def test_banner_falls_back_to_plain_text_when_not_a_terminal(capsys) -> None:
    """A piped transcript should not have to read around the artwork."""
    print_banner(Console(force_terminal=False, width=200))

    printed = capsys.readouterr().out
    assert "ORIS" in printed
    assert "Orchestrator / Research / Analysis" in printed
    assert "█" not in printed


def test_banner_falls_back_on_a_narrow_terminal(capsys) -> None:
    """Below the art's width it wraps into noise, so the title is used instead."""
    print_banner(Console(force_terminal=True, width=20))

    printed = capsys.readouterr().out
    assert "█" not in printed


def test_cli_history_persists_across_runs(tmp_path) -> None:
    """Input history survives a restart so the up arrow spans sessions."""
    history_path = tmp_path / "cli_history"
    readline.clear_history()
    try:
        with cli_history(history_path):
            readline.add_history("first question")
            readline.add_history("second question")

        assert history_path.exists()

        readline.clear_history()
        with cli_history(history_path):
            recalled = [
                readline.get_history_item(index)
                for index in range(1, readline.get_current_history_length() + 1)
            ]
    finally:
        readline.clear_history()

    assert recalled == ["first question", "second question"]


def test_cli_history_starts_empty_without_a_history_file(tmp_path) -> None:
    """A first run has no history file and must not fail on startup."""
    readline.clear_history()
    try:
        with cli_history(tmp_path / "nested" / "cli_history"):
            readline.add_history("only question")
    finally:
        readline.clear_history()

    assert (tmp_path / "nested" / "cli_history").exists()


def test_run_chat_uses_automatic_routing_by_default(
    monkeypatch, capsys, tmp_path
) -> None:
    """Ordinary input requests the constrained parent-graph router."""
    knowledge_repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    graph = streaming_graph({"messages": [AIMessage(content="Hello.")]})
    responses = iter(["", "Hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    request = graph.calls.call_args.args[0]
    config = graph.calls.call_args.args[1]
    assert request["messages"][0].content == "Hello"
    assert request["mode"] == "auto"
    assert config == {"configurable": {"thread_id": "session-1"}}
    assert graph.calls.call_args.kwargs == {
        "durability": "sync",
        # Steps come from inside the specialists, which is where the time goes.
        "stream_mode": ["values", "debug"],
        "subgraphs": True,
    }
    assert graph.calls.call_count == 1
    document = knowledge_repository.search("Hello")[0]
    assert isinstance(document, KnowledgeDocument)
    assert document.source_type == "chat"
    assert document.source_ref == "session-1"
    assert document.title == "Hello"
    assert document.content == "User:\nHello\n\nORIS:\nHello."
    printed = capsys.readouterr().out
    assert "ORIS" in printed
    assert "Hello." in printed


def test_run_chat_uses_web_research_command(monkeypatch, tmp_path) -> None:
    """The research command selects Web Research and removes the command."""
    knowledge_repository = Mock()
    graph = streaming_graph(
        {"messages": [AIMessage(content="A cited research answer [1].")]}
    )
    responses = iter(["/research What is LangGraph?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    request = graph.calls.call_args.args[0]
    config = graph.calls.call_args.args[1]
    assert request["messages"][0].content == "What is LangGraph?"
    assert request["mode"] == "web_research"
    assert config == {"configurable": {"thread_id": "session-1"}}
    assert knowledge_repository.add_exchange.call_count == 1


def test_run_chat_uses_local_knowledge_command(monkeypatch, tmp_path) -> None:
    """Recall selects Local Knowledge without archiving a derived answer."""
    knowledge_repository = Mock()
    graph = streaming_graph(
        {"messages": [AIMessage(content="We chose schedules.toml [1].")]}
    )
    responses = iter(["/recall What did we decide about scheduling?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    request = graph.calls.call_args.args[0]
    config = graph.calls.call_args.args[1]
    assert request["messages"][0].content == ("What did we decide about scheduling?")
    assert request["mode"] == "local_knowledge"
    assert config == {"configurable": {"thread_id": "session-1"}}
    assert (
        knowledge_repository.add_exchange.call_args.kwargs["selected_mode"]
        == "local_knowledge"
    )


def test_run_chat_does_not_archive_automatically_routed_recall(
    monkeypatch, tmp_path
) -> None:
    """An automatically selected Local Knowledge answer is not re-indexed."""
    knowledge_repository = Mock()
    graph = streaming_graph(
        {
            "messages": [AIMessage(content="The retained decision [1].")],
            "selected_mode": "local_knowledge",
        }
    )
    responses = iter(["What did we decide earlier?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    request = graph.calls.call_args.args[0]
    assert request["mode"] == "auto"
    assert (
        knowledge_repository.add_exchange.call_args.kwargs["selected_mode"]
        == "local_knowledge"
    )


def test_run_chat_uses_community_research_command(monkeypatch, tmp_path) -> None:
    """The community command selects its fixed specialist and strips the command."""
    graph = streaming_graph({"messages": [AIMessage(content="Community answer.")]})
    knowledge_repository = Mock()
    responses = iter(["/community LangGraph", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    request = graph.calls.call_args.args[0]
    assert request["messages"][0].content == "LangGraph"
    assert request["mode"] == "community_research"
    assert knowledge_repository.add_exchange.call_count == 1


def test_run_chat_rejects_community_without_a_topic(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    """A bare community command prints its usage without invoking the graph."""
    graph = Mock()
    knowledge_repository = Mock()
    responses = iter(["/community", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    graph.calls.assert_not_called()
    knowledge_repository.add_exchange.assert_not_called()
    assert "Usage: /community <topic>" in capsys.readouterr().out


def test_run_chat_rejects_recall_without_a_question(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    """A bare recall command prints its usage without invoking the graph."""
    graph = Mock()
    knowledge_repository = Mock()
    responses = iter(["/recall", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    graph.calls.assert_not_called()
    knowledge_repository.add_exchange.assert_not_called()
    assert "Usage: /recall <question>" in capsys.readouterr().out


def test_run_chat_rejects_an_unknown_slash_command(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    """A misspelled command cannot fall through to ordinary chat."""
    graph = Mock()
    knowledge_repository = Mock()
    responses = iter(["/resaerch latest Pistons information", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    graph.calls.assert_not_called()
    knowledge_repository.add_exchange.assert_not_called()
    assert "Unknown command: /resaerch" in capsys.readouterr().out


def test_run_chat_does_not_index_a_failed_exchange(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    """A handled request failure returns to the prompt without being indexed."""
    graph = streaming_graph(
        {
            "messages": [],
            "request_succeeded": False,
            "request_error": "Web Research failed: provider unavailable",
        }
    )
    knowledge_repository = Mock()
    responses = iter(["Hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=tmp_path / "current_session",
            thread_id="session-1",
        )
    )

    knowledge_repository.add_exchange.assert_not_called()
    assert "Web Research failed: provider unavailable" in capsys.readouterr().out


def test_run_chat_starts_and_uses_a_new_session(monkeypatch, capsys, tmp_path) -> None:
    """The session commands switch later requests to a fresh thread ID."""
    graph = streaming_graph({"messages": [AIMessage(content="Hello.")]})
    knowledge_repository = Mock()
    session_file_path = tmp_path / "current_session"
    responses = iter(["/session", "/new", "Hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    asyncio.run(
        run_chat(
            graph,
            knowledge_repository,
            session_file_path=session_file_path,
            thread_id="session-1",
        )
    )

    new_session_id = session_file_path.read_text(encoding="utf-8").strip()
    config = graph.calls.call_args.args[1]
    assert new_session_id != "session-1"
    assert config == {"configurable": {"thread_id": new_session_id}}
    archived = knowledge_repository.add_exchange.call_args.kwargs
    assert archived["thread_id"] == new_session_id
    output = capsys.readouterr().out
    assert "Current session: session-1" in output
    assert f"Started new session: {new_session_id}" in output
