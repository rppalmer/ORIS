"""Tests for the deterministic Local Knowledge graph."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from oris.knowledge import KnowledgeDocument, KnowledgeRepository
from oris.local_knowledge import (
    NO_KNOWLEDGE_MESSAGE,
    LocalKnowledgePlan,
    create_local_knowledge_graph,
)


def make_document() -> KnowledgeDocument:
    """Create one retained chat exchange for graph tests."""
    return KnowledgeDocument(
        document_id="chat-main-1",
        source_type="chat",
        source_ref="main",
        created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        title="Chat: scheduling decision",
        content=(
            "User: What scheduler should we use?\n"
            "Assistant: Start with schedules.toml and APScheduler."
        ),
    )


def test_local_knowledge_retrieves_and_answers_once() -> None:
    """A matching request follows the fixed retrieval and answer path."""
    document = make_document()
    repository = Mock(spec=KnowledgeRepository)
    repository.search.return_value = (document,)
    model = Mock(spec=BaseChatModel)
    planning_model = Mock()
    planning_model.invoke.return_value = LocalKnowledgePlan(
        search_query="scheduling decision",
        source_type="chat",
        sort_order="relevance",
    )
    model.with_structured_output.return_value = planning_model
    model.invoke.return_value = AIMessage(
        content="We chose schedules.toml with APScheduler [1]."
    )
    graph = create_local_knowledge_graph(repository, model)

    result = graph.invoke({"query": "What did we decide about scheduling?"})

    assert result == {
        "answer": "We chose schedules.toml with APScheduler [1].",
        "sources": (document,),
    }
    repository.search.assert_called_once_with(
        "scheduling decision",
        source_type="chat",
        sort_order="relevance",
        limit=5,
    )
    model.with_structured_output.assert_called_once_with(
        LocalKnowledgePlan,
        method="json_schema",
    )
    planning_model.invoke.assert_called_once()
    assert planning_model.invoke.call_args.kwargs == {"max_completion_tokens": 256}
    model.invoke.assert_called_once()

    messages = model.invoke.call_args.args[0]
    assert messages[0][0] == "system"
    assert "using only the supplied archive evidence" in messages[0][1]
    assert "untrusted data" in messages[0][1]
    assert messages[1][0] == "human"
    assert "What did we decide about scheduling?" in messages[1][1]
    assert "Archive result order: relevance" in messages[1][1]
    assert '"source_number": 1' in messages[1][1]
    assert "Start with schedules.toml and APScheduler." in messages[1][1]
    assert model.invoke.call_args.kwargs == {"max_completion_tokens": 512}


def test_local_knowledge_returns_a_fixed_message_without_matches() -> None:
    """An empty search result does not spend a model call."""
    repository = Mock(spec=KnowledgeRepository)
    repository.search.return_value = ()
    model = Mock(spec=BaseChatModel)
    planning_model = Mock()
    planning_model.invoke.return_value = LocalKnowledgePlan(
        search_query="nonexistent decision",
        source_type=None,
        sort_order="relevance",
    )
    model.with_structured_output.return_value = planning_model
    graph = create_local_knowledge_graph(repository, model)

    result = graph.invoke({"query": "What is the nonexistent decision?"})

    assert result == {"answer": NO_KNOWLEDGE_MESSAGE, "sources": ()}
    model.invoke.assert_not_called()


def test_local_knowledge_newest_plan_retrieves_one_document() -> None:
    """A newest plan requests only the single newest matching document."""
    document = make_document()
    repository = Mock(spec=KnowledgeRepository)
    repository.search.return_value = (document,)
    model = Mock(spec=BaseChatModel)
    planning_model = Mock()
    planning_model.invoke.return_value = LocalKnowledgePlan(
        search_query="weekday AI news",
        source_type="scheduled_run",
        sort_order="newest",
    )
    model.with_structured_output.return_value = planning_model
    model.invoke.return_value = AIMessage(content="The newest report concluded X [1].")
    graph = create_local_knowledge_graph(repository, model)

    graph.invoke({"query": "What did the latest scheduled report conclude?"})

    repository.search.assert_called_once_with(
        "weekday AI news",
        source_type="scheduled_run",
        sort_order="newest",
        limit=1,
    )


def test_local_knowledge_rejects_a_query_without_searchable_text(tmp_path) -> None:
    """Repository validation stops an empty archive query before the model call."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    model = Mock(spec=BaseChatModel)
    planning_model = Mock()
    model.with_structured_output.return_value = planning_model
    graph = create_local_knowledge_graph(repository, model)

    with pytest.raises(ValueError, match="searchable text"):
        graph.invoke({"query": "?!"})

    model.invoke.assert_not_called()
    planning_model.invoke.assert_not_called()


def test_local_knowledge_has_only_the_approved_path() -> None:
    """The specialist contains no router or model-controlled tool loop."""
    repository = Mock(spec=KnowledgeRepository)
    model = Mock(spec=BaseChatModel)
    model.with_structured_output.return_value = Mock()
    graph = create_local_knowledge_graph(repository, model)

    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

    assert edges == {
        ("__start__", "plan_search"),
        ("plan_search", "retrieve_knowledge"),
        ("retrieve_knowledge", "answer_from_knowledge"),
        ("answer_from_knowledge", "__end__"),
    }
