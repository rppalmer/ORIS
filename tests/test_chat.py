"""Tests for the chat-shaped ORIS graph."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from oris.chat import (
    LazyMCPSpecialist,
    RoutingDecision,
    create_oris_graph,
)
from oris.knowledge import KnowledgeDocument
from oris.search import WebSearchResult
from oris.web_research import CitedAnswer


def test_oris_chats_without_web_research_by_default() -> None:
    """Ordinary chat uses the model and does not search the web."""
    web_research_graph = Mock()
    local_knowledge_graph = Mock()
    model = Mock()
    model.invoke.return_value = AIMessage(content="A direct response.")
    graph = create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        Mock(),
        model,
    )

    result = graph.invoke({"messages": [HumanMessage(content="Hello")]})

    messages = model.invoke.call_args.args[0]
    assert messages[-1].content == "Hello"
    assert result["messages"][-1].content == "A direct response."
    web_research_graph.ainvoke.assert_not_called()
    local_knowledge_graph.invoke.assert_not_called()


def test_oris_delegates_explicit_research_to_web_research() -> None:
    """One human message becomes one sourced assistant response."""
    web_research_graph = Mock()
    web_research_graph.ainvoke = AsyncMock()
    web_research_graph.ainvoke.return_value = {
        "answer": CitedAnswer(answer="LangGraph supports stateful workflows [1]."),
        "sources": (
            WebSearchResult(
                title="LangGraph overview",
                url="https://docs.langchain.com/oss/python/langgraph/overview",
                snippet="LangGraph supports stateful agent workflows.",
                relevance_score=0.95,
            ),
        ),
    }
    local_knowledge_graph = Mock()
    model = Mock()
    graph = create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        Mock(),
        model,
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="What is LangGraph?")],
                "mode": "web_research",
            }
        )
    )

    web_research_graph.ainvoke.assert_called_once_with({"query": "What is LangGraph?"})
    local_knowledge_graph.invoke.assert_not_called()
    model.invoke.assert_not_called()
    request = result["messages"][0]
    assert isinstance(request, HumanMessage)
    assert request.content == "What is LangGraph?"
    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert "LangGraph supports stateful workflows [1]." in response.content
    assert (
        "[1] [LangGraph overview]"
        "(https://docs.langchain.com/oss/python/langgraph/overview)" in response.content
    )


def test_oris_delegates_explicit_local_knowledge_request() -> None:
    """One human message becomes one answer from retained local evidence."""
    web_research_graph = Mock()
    local_knowledge_graph = Mock()
    local_knowledge_graph.invoke.return_value = {
        "answer": "We chose schedules.toml with APScheduler [1].",
        "sources": (
            KnowledgeDocument(
                document_id="chat-main-1",
                source_type="chat",
                source_ref="main",
                created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
                title="Chat: scheduling decision",
                content="Use schedules.toml with APScheduler.",
            ),
        ),
    }
    model = Mock()
    graph = create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        Mock(),
        model,
    )

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="What did we decide about scheduling?")],
            "mode": "local_knowledge",
        }
    )

    local_knowledge_graph.invoke.assert_called_once_with(
        {"query": "What did we decide about scheduling?"}
    )
    web_research_graph.ainvoke.assert_not_called()
    model.invoke.assert_not_called()
    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert "We chose schedules.toml with APScheduler [1]." in response.content
    assert "Archive sources:" in response.content
    assert "[1] Chat: scheduling decision (chat: main)" in response.content


def test_oris_delegates_explicit_community_research() -> None:
    """The parent uses the fixed asynchronous Community Research wrapper."""
    community_research_graph = Mock()
    community_research_graph.ainvoke = AsyncMock(
        return_value={
            "answer": "LangGraph is being discussed for agent workflows.",
            "cited_urls": ["https://news.ycombinator.com/item?id=123"],
            "research_result": {"call_id": "net-razor-call-1"},
        }
    )
    web_research_graph = Mock()
    local_knowledge_graph = Mock()
    model = Mock()
    graph = create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        community_research_graph,
        model,
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="LangGraph")],
                "mode": "community_research",
            }
        )
    )

    community_research_graph.ainvoke.assert_awaited_once_with({"topic": "LangGraph"})
    web_research_graph.ainvoke.assert_not_called()
    local_knowledge_graph.invoke.assert_not_called()
    model.invoke.assert_not_called()
    response = result["messages"][-1]
    assert response.content == (
        "LangGraph is being discussed for agent workflows.\n\n"
        "Community sources:\n"
        "[1](https://news.ycombinator.com/item?id=123)"
    )


def test_oris_uses_the_constrained_router_in_auto_mode() -> None:
    """One structured route selects one fixed specialist."""
    routing_model = Mock()
    routing_model.invoke.return_value = RoutingDecision(
        route="community_research",
        resolved_request="LangGraph",
    )
    model = Mock()
    model.with_structured_output.return_value = routing_model
    community_research_graph = Mock()
    community_research_graph.ainvoke = AsyncMock(
        return_value={
            "answer": "Community answer.",
            "cited_urls": [],
            "research_result": {"call_id": "net-razor-call-1"},
        }
    )
    web_research_graph = Mock()
    local_knowledge_graph = Mock()
    graph = create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        community_research_graph,
        model,
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content="What are people on X saying about LangGraph?")
                ],
                "mode": "auto",
            }
        )
    )

    model.with_structured_output.assert_called_once_with(
        RoutingDecision,
        method="json_schema",
    )
    routing_model.invoke.assert_called_once()
    assert routing_model.invoke.call_args.kwargs == {"max_completion_tokens": 256}
    community_research_graph.ainvoke.assert_awaited_once_with({"topic": "LangGraph"})
    web_research_graph.ainvoke.assert_not_called()
    local_knowledge_graph.invoke.assert_not_called()
    assert result["messages"][-1].content == "Community answer."


def test_oris_explicit_mode_bypasses_the_router() -> None:
    """A caller-selected mode remains a deterministic override."""
    routing_model = Mock()
    model = Mock()
    model.with_structured_output.return_value = routing_model
    model.invoke.return_value = AIMessage(content="Direct answer.")
    graph = create_oris_graph(Mock(), Mock(), Mock(), model)

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Hello")],
            "mode": "chat",
        }
    )

    routing_model.invoke.assert_not_called()
    assert result["messages"][-1].content == "Direct answer."


def test_oris_passes_a_resolved_follow_up_to_research() -> None:
    """The router sees conversation context and resolves one specialist request."""
    routing_model = Mock()
    routing_model.invoke.return_value = RoutingDecision(
        route="web_research",
        resolved_request="What is today's weather for ZIP code 48383?",
    )
    model = Mock()
    model.with_structured_output.return_value = routing_model
    web_research_graph = Mock()
    web_research_graph.ainvoke = AsyncMock()
    web_research_graph.ainvoke.return_value = {
        "answer": CitedAnswer(answer="White Lake is sunny [1]."),
        "sources": (
            WebSearchResult(
                title="White Lake weather",
                url="https://example.com/weather",
                snippet="Sunny conditions in White Lake.",
            ),
        ),
    }
    graph = create_oris_graph(
        web_research_graph,
        Mock(),
        Mock(),
        model,
    )

    asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content="What's the weather today?"),
                    AIMessage(content="What location should I check?"),
                    HumanMessage(content="My ZIP code is 48383."),
                ],
                "mode": "auto",
            }
        )
    )

    routing_messages = routing_model.invoke.call_args.args[0]
    assert isinstance(routing_messages[0], SystemMessage)
    assert [message.content for message in routing_messages[1:]] == [
        "What's the weather today?",
        "What location should I check?",
        "My ZIP code is 48383.",
    ]
    web_research_graph.ainvoke.assert_called_once_with(
        {"query": "What is today's weather for ZIP code 48383?"}
    )


def test_oris_closes_a_router_failure() -> None:
    """A failed router cannot leave an orphaned human turn."""
    routing_model = Mock()
    routing_model.invoke.side_effect = RuntimeError("router unavailable")
    model = Mock()
    model.with_structured_output.return_value = routing_model
    graph = create_oris_graph(Mock(), Mock(), Mock(), model)

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Route this request")],
            "mode": "auto",
        }
    )

    assert result["request_succeeded"] is False
    assert result["request_error"] == "Request routing failed: router unavailable"
    assert result["messages"] == []
    model.invoke.assert_not_called()


def test_oris_reports_a_missing_human_message_as_a_closed_request() -> None:
    """Every node, including validation, reports failure as state not an exception."""
    web_research_graph = Mock()
    local_knowledge_graph = Mock()
    model = Mock()
    graph = create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        Mock(),
        model,
    )

    result = graph.invoke({"messages": [AIMessage(content="No new request.")]})

    assert result["request_succeeded"] is False
    assert result["request_error"] == (
        "Request validation failed: ORIS requires a human message"
    )
    web_research_graph.ainvoke.assert_not_called()
    local_knowledge_graph.invoke.assert_not_called()
    model.invoke.assert_not_called()


def test_oris_restores_conversation_after_restart(tmp_path) -> None:
    """A reopened SQLite checkpointer restores the thread's messages."""
    database_path = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "main"}}

    first_model = Mock()
    first_model.invoke.return_value = AIMessage(content="My name is ORIS.")
    with SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        first_graph = create_oris_graph(
            Mock(),
            Mock(),
            Mock(),
            first_model,
            checkpointer=checkpointer,
        )
        first_graph.invoke(
            {"messages": [HumanMessage(content="What is your name?")]},
            config,
            durability="sync",
        )

    second_model = Mock()
    second_model.invoke.return_value = AIMessage(content="I already told you.")
    with SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        restarted_graph = create_oris_graph(
            Mock(),
            Mock(),
            Mock(),
            second_model,
            checkpointer=checkpointer,
        )
        restarted_graph.invoke(
            {"messages": [HumanMessage(content="What did you say?")]},
            config,
            durability="sync",
        )

    restored_messages = second_model.invoke.call_args.args[0]
    assert isinstance(restored_messages[0], SystemMessage)
    assert [message.content for message in restored_messages[1:]] == [
        "What is your name?",
        "My name is ORIS.",
        "What did you say?",
    ]


def test_oris_closes_a_failed_turn_before_the_next_request(
    tmp_path,
) -> None:
    """A specialist failure becomes an assistant turn instead of orphaned input."""
    database_path = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "main"}}
    web_research_graph = Mock()
    web_research_graph.ainvoke = AsyncMock(
        side_effect=RuntimeError("model unavailable")
    )
    direct_chat_model = Mock()
    direct_chat_model.invoke.return_value = AIMessage(content="Pistons answer.")

    # The asynchronous checkpointer, because the graph reaches Web Research
    # through an asynchronous node now and the synchronous saver refuses async
    # methods outright. This is the same pairing the application itself uses.
    async def drive() -> dict:
        async with AsyncSqliteSaver.from_conn_string(
            str(database_path)
        ) as checkpointer:
            graph = create_oris_graph(
                web_research_graph,
                Mock(),
                Mock(),
                direct_chat_model,
                checkpointer=checkpointer,
            )
            failed = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content="Latest UAP information")],
                    "mode": "web_research",
                },
                config,
                durability="sync",
            )
            await graph.ainvoke(
                {
                    "messages": [HumanMessage(content="Latest Pistons information")],
                    "mode": "chat",
                },
                config,
                durability="sync",
            )
            return failed

    failed_result = asyncio.run(drive())

    assert failed_result["request_succeeded"] is False
    assert failed_result["request_error"] == "Web Research failed: model unavailable"
    assert failed_result["messages"] == []
    messages = direct_chat_model.invoke.call_args.args[0]
    assert isinstance(messages[0], SystemMessage)
    # Without today's date, direct chat answers as of its training cutoff and
    # has no way to know that it is doing so.
    assert date.today().isoformat() in messages[0].content
    assert [message.content for message in messages[1:]] == [
        "Latest Pistons information"
    ]


def test_router_cannot_select_threat_intel() -> None:
    """Third-party indicator egress stays an explicit user choice.

    The router's structured-output schema is the enforcement point: Threat Intel
    is unreachable from a model decision because the model cannot express it.
    """
    route_schema = RoutingDecision.model_json_schema()["properties"]["route"]

    assert "threat_intel" not in route_schema["enum"]
    assert set(route_schema["enum"]) == {
        "chat",
        "community_research",
        "local_knowledge",
        "podcast_catch_up",
        "web_research",
    }


def test_oris_delegates_explicit_threat_intel_request() -> None:
    """The /threat path reaches the specialist and reports examined indicators."""
    threat_intel_graph = Mock()
    threat_intel_graph.ainvoke = AsyncMock(
        return_value={
            "answer": "VirusTotal reports 3 detections.",
            "indicators": ["45.83.192.4"],
            "sources_used": ["45.83.192.4"],
            "source_status": {
                "virustotal": "ok",
                "sentinel": "missing_api_key",
            },
        }
    )
    model = Mock()
    graph = create_oris_graph(
        Mock(),
        Mock(),
        Mock(),
        model,
        threat_intel_graph=threat_intel_graph,
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="45.83.192.4")],
                "mode": "threat_intel",
            },
            {"configurable": {"thread_id": "5a1c-conversation"}},
        )
    )

    threat_intel_graph.ainvoke.assert_awaited_once_with(
        {"request": "45.83.192.4", "thread_id": "5a1c-conversation"}
    )
    model.invoke.assert_not_called()
    assert result["messages"][-1].content == (
        "VirusTotal reports 3 detections.\n\n"
        "Indicators examined:\n- 45.83.192.4\n\n"
        "Sources: 1 answered, 1 failed\n\n"
        "| Source | Result |\n"
        "| --- | --- |\n"
        "| sentinel | **missing_api_key** |\n"
        "| virustotal | ok |"
    )


def test_threat_intel_reports_clearly_when_threatsyft_is_not_configured() -> None:
    """An unconfigured optional capability degrades one request, not the core."""
    model = Mock()
    model.invoke.return_value = AIMessage(content="Direct answer.")
    graph = create_oris_graph(Mock(), Mock(), Mock(), model)

    failed = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="45.83.192.4")],
                "mode": "threat_intel",
            }
        )
    )

    assert failed["request_succeeded"] is False
    assert "Threat Intel failed" in failed["request_error"]
    assert "THREATSYFT_ROOT" in failed["request_error"]
    assert failed["messages"] == []

    succeeded = graph.invoke(
        {"messages": [HumanMessage(content="Hello")], "mode": "chat"}
    )
    assert succeeded["messages"][-1].content == "Direct answer."


def test_oris_bounds_the_history_sent_to_the_model() -> None:
    """A long thread cannot grow past the configured context budget."""
    model = Mock()
    model.invoke.return_value = AIMessage(content="Answer.")
    graph = create_oris_graph(Mock(), Mock(), Mock(), model, max_history_tokens=40)
    history = []
    for index in range(8):
        history.append(HumanMessage(content=f"question {index} " + "word " * 20))
        history.append(AIMessage(content=f"answer {index} " + "word " * 20))
    history.append(HumanMessage(content="latest question"))

    graph.invoke({"messages": history, "mode": "chat"})

    sent_messages = model.invoke.call_args.args[0]
    assert isinstance(sent_messages[0], SystemMessage)
    assert len(sent_messages) < len(history)
    assert sent_messages[-1].content == "latest question"


def test_oris_keeps_a_request_larger_than_the_history_budget() -> None:
    """Trimming never leaves the model a prompt with no request to answer."""
    model = Mock()
    model.invoke.return_value = AIMessage(content="Answer.")
    graph = create_oris_graph(Mock(), Mock(), Mock(), model, max_history_tokens=10)
    oversized_request = "word " * 500

    graph.invoke(
        {"messages": [HumanMessage(content=oversized_request)], "mode": "chat"}
    )

    sent_messages = model.invoke.call_args.args[0]
    assert isinstance(sent_messages[0], SystemMessage)
    assert [message.content for message in sent_messages[1:]] == [oversized_request]


def test_lazy_mcp_specialist_is_not_built_until_it_is_used() -> None:
    """An MCP-backed specialist is resolved on first use, then reused."""
    specialist_graph = Mock()
    specialist_graph.ainvoke = AsyncMock(
        return_value={"answer": "ok", "cited_urls": []}
    )
    build = AsyncMock(return_value=specialist_graph)

    specialist = LazyMCPSpecialist(build)
    build.assert_not_awaited()

    async def run_twice() -> None:
        await specialist.ainvoke({"topic": "first"})
        await specialist.ainvoke({"topic": "second"})

    asyncio.run(run_twice())

    build.assert_awaited_once()
    assert specialist_graph.ainvoke.await_count == 2


def test_unavailable_mcp_server_closes_only_its_own_request() -> None:
    """ADR 001: an absent MCP server degrades one capability, not the core."""
    build = AsyncMock(side_effect=RuntimeError("Net-Razor is not installed"))
    model = Mock()
    model.invoke.return_value = AIMessage(content="Direct answer.")
    graph = create_oris_graph(
        Mock(),
        Mock(),
        LazyMCPSpecialist(build),
        model,
    )

    failed = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="What is X saying?")],
                "mode": "community_research",
            }
        )
    )

    assert failed["request_succeeded"] is False
    assert failed["request_error"] == (
        "Community Research failed: Net-Razor is not installed"
    )
    assert failed["messages"] == []

    succeeded = graph.invoke(
        {"messages": [HumanMessage(content="Hello")], "mode": "chat"}
    )

    assert succeeded["messages"][-1].content == "Direct answer."


def test_oris_async_checkpointer_restores_community_session(
    tmp_path,
) -> None:
    """The async CLI persistence path restores a completed community turn."""
    database_path = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "main"}}

    async def run_session() -> list[str]:
        community_graph = Mock()
        community_graph.ainvoke = AsyncMock(
            return_value={
                "answer": "Community answer.",
                "cited_urls": [],
                "research_result": {"call_id": "net-razor-call-1"},
            }
        )
        first_model = Mock()
        async with AsyncSqliteSaver.from_conn_string(
            str(database_path)
        ) as checkpointer:
            first_graph = create_oris_graph(
                Mock(),
                Mock(),
                community_graph,
                first_model,
                checkpointer=checkpointer,
            )
            await first_graph.ainvoke(
                {
                    "messages": [HumanMessage(content="LangGraph")],
                    "mode": "community_research",
                },
                config,
                durability="sync",
            )

        second_model = Mock()
        second_model.invoke.return_value = AIMessage(content="Follow-up answer.")
        async with AsyncSqliteSaver.from_conn_string(
            str(database_path)
        ) as checkpointer:
            restarted_graph = create_oris_graph(
                Mock(),
                Mock(),
                Mock(),
                second_model,
                checkpointer=checkpointer,
            )
            await restarted_graph.ainvoke(
                {
                    "messages": [HumanMessage(content="Continue")],
                    "mode": "chat",
                },
                config,
                durability="sync",
            )
        return [
            message.content
            for message in second_model.invoke.call_args.args[0]
            if not isinstance(message, SystemMessage)
        ]

    restored_messages = asyncio.run(run_session())

    assert restored_messages == [
        "LangGraph",
        "Community answer.",
        "Continue",
    ]


def _podcast_graph_and_double() -> tuple[object, Mock]:
    """A parent graph wired to a controllable Podcast Catch-up double."""
    podcast_graph = Mock()
    podcast_graph.ainvoke = AsyncMock(
        return_value={
            "answer": "One episode was summarized.",
            "cited_urls": ["https://example.com/episode-1"],
            "episodes": [],
            "caveats": [],
        }
    )
    community_research_graph = Mock()
    community_research_graph.ainvoke = AsyncMock()
    graph = create_oris_graph(
        Mock(),
        Mock(),
        community_research_graph,
        Mock(),
        podcast_catch_up_graph=podcast_graph,
    )
    return graph, podcast_graph


def test_a_bare_podcast_command_catches_up_on_every_feed() -> None:
    """`/podcasts` sends an empty request, which means the whole catch-up."""
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="")], "mode": "podcast_catch_up"}
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with({"thread_id": ""})


def test_a_named_show_narrows_the_podcast_run() -> None:
    """`/podcasts <show>` asks what one show's latest episode said."""
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="LINUX Unplugged")],
                "mode": "podcast_catch_up",
            }
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with(
        {"thread_id": "", "show": "LINUX Unplugged"}
    )


def test_recap_asks_for_episodes_that_already_have_transcripts() -> None:
    """`/podcasts recap` is the only route back to a scheduled run's work.

    That run acknowledged its episodes, so Net-Razor leaves them out of the
    catch-up queue from then on and an ordinary catch-up the next morning
    reports nothing new.
    """
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="recap")],
                "mode": "podcast_catch_up",
            }
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with(
        {"thread_id": "", "include_processed": True}
    )


def test_recap_and_a_show_name_are_independent() -> None:
    """Which episodes to read and whose are two separate parts of the request."""
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="Recap LINUX Unplugged")],
                "mode": "podcast_catch_up",
            }
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with(
        {"thread_id": "", "include_processed": True, "show": "LINUX Unplugged"}
    )


def test_a_show_whose_name_merely_starts_with_recap_is_still_a_show() -> None:
    """The word is only the mode when it stands alone at the front.

    A show actually called "Recapped" must not have its first word eaten.
    """
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="Recapped Weekly")],
                "mode": "podcast_catch_up",
            }
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with(
        {"thread_id": "", "show": "Recapped Weekly"}
    )


def test_the_episode_list_says_where_each_transcript_came_from() -> None:
    """The reader has to be able to tell machine words from the publisher's.

    Whisper gets names, acronyms, and version numbers wrong, so how much of an
    episode summary to trust depends entirely on which of these it was. The
    list used to be bare numbered links built from the digest's citations: no
    titles, no shows, no provenance, and nothing at all when the digest cited
    nothing.
    """
    graph, podcast_graph = _podcast_graph_and_double()
    podcast_graph.ainvoke = AsyncMock(
        return_value={
            "answer": "Two episodes were summarized.",
            "cited_urls": [],
            "episodes": [
                {
                    "episode_id": "episode-1",
                    "title": "Episode 1",
                    "show": "Example Show",
                    "published_at": "2026-08-01T12:00:00+00:00",
                    "url": "https://example.com/episode-1",
                    "summary": "Summary 1",
                    "transcript_backend": "publisher",
                    "transcript_created_now": False,
                    "transcript_truncated": False,
                },
                {
                    "episode_id": "episode-2",
                    "title": "Episode 2",
                    "show": "Other Show",
                    "published_at": "2026-08-02T12:00:00+00:00",
                    "url": "https://example.com/episode-2",
                    "summary": "Summary 2",
                    "transcript_backend": "whisper",
                    "transcript_created_now": True,
                    "transcript_truncated": False,
                },
            ],
            "caveats": [],
        }
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="")], "mode": "podcast_catch_up"}
        )
    )

    answer = result["messages"][-1].content
    assert (
        "1. [Episode 1](https://example.com/episode-1) — Example Show, "
        "publisher's transcript" in answer
    )
    assert (
        "2. [Episode 2](https://example.com/episode-2) — Other Show, "
        "transcribed by ORIS during this run" in answer
    )


def test_list_asks_which_shows_are_configured() -> None:
    """`/podcasts list` answers a question about configuration, not episodes."""
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="list")], "mode": "podcast_catch_up"}
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with(
        {"thread_id": "", "list_shows": True}
    )


def test_a_show_called_list_something_is_still_a_show() -> None:
    """Only a bare "list" is the command; anything after it names a show.

    Unlike `recap`, which narrows, listing takes no subject — so a second word
    means the first was never the command.
    """
    graph, podcast_graph = _podcast_graph_and_double()

    asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="List Notes Weekly")],
                "mode": "podcast_catch_up",
            }
        )
    )

    podcast_graph.ainvoke.assert_awaited_once_with(
        {"thread_id": "", "show": "List Notes Weekly"}
    )
