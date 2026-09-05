"""Tests for the deterministic Local Knowledge graph."""

from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from oris.knowledge import KnowledgeDocument, KnowledgeRepository
from oris.local_knowledge import (
    MAX_DOCUMENT_CHARACTERS,
    NO_KNOWLEDGE_MESSAGE,
    TRUNCATION_NOTICE,
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
    # Archived documents carry dates; without today's, "the newest report" and
    # "recent" have no reference point.
    assert date.today().isoformat() in messages[0][1]
    assert messages[1][0] == "human"
    assert "What did we decide about scheduling?" in messages[1][1]
    assert "Archive result order: relevance" in messages[1][1]
    assert '"source_number": 1' in messages[1][1]
    assert "Start with schedules.toml and APScheduler." in messages[1][1]
    assert model.invoke.call_args.kwargs == {"max_completion_tokens": 1024}


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


def test_local_knowledge_newest_plan_still_retrieves_several_documents() -> None:
    """Recency ordering changes which document comes first, not how many arrive.

    This used to request exactly one document, to keep a recurring-report
    question answered from the newest run rather than blended across several.
    The system prompt already says that, and enforcing it here instead applied
    it to every question the planner reads as recency-sensitive: a real archive
    answered "what has been decided about how ORIS schedules its own jobs" from
    one unrelated chat, because one was all it was allowed to see.
    """
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
        limit=5,
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


def _graph_answering(answer: str, sources: tuple[KnowledgeDocument, ...]) -> object:
    """Compile the graph around one fixed retrieval and one fixed answer."""
    repository = Mock(spec=KnowledgeRepository)
    repository.search.return_value = sources
    model = Mock(spec=BaseChatModel)
    planning_model = Mock()
    planning_model.invoke.return_value = LocalKnowledgePlan(
        search_query="scheduling decision",
        source_type="chat",
        sort_order="relevance",
    )
    model.with_structured_output.return_value = planning_model
    model.invoke.return_value = AIMessage(content=answer)
    return create_local_knowledge_graph(repository, model), model


def test_an_answer_citing_a_source_that_was_not_retrieved_is_rejected() -> None:
    """A citation the reader cannot follow is worse than no answer at all.

    The archive is the user's own history, so a number pointing at nothing
    reads as corroboration that does not exist. Every other specialist that
    cites its evidence checks this; this one silently did not.
    """
    graph, _ = _graph_answering(
        "We chose schedules.toml [1], and rejected cron [4].", (make_document(),)
    )

    with pytest.raises(ValueError, match=r"cited unavailable sources: \[4\]"):
        graph.invoke({"query": "What did we decide about scheduling?"})


def test_an_answer_that_cites_nothing_is_allowed_through() -> None:
    """This specialist is told to say when the archive cannot answer.

    That answer is honest precisely because it cites nothing, so requiring a
    citation the way Web Research does would fail the one response the prompt
    explicitly asks for. The asymmetry between the two is deliberate.
    """
    graph, _ = _graph_answering(
        "The archive has nothing about scheduling.", (make_document(),)
    )

    result = graph.invoke({"query": "What did we decide about scheduling?"})

    assert result["answer"] == "The archive has nothing about scheduling."


def test_one_oversized_archived_document_cannot_fill_the_prompt() -> None:
    """Retrieval returns whole prior exchanges, and nothing bounds their size.

    A `/threat report` turn archives its entire evidence pivot — one real
    single-indicator report is over 5,000 characters — and five retrieved
    together would crowd out the question and the instructions. The cut is
    announced so the model treats the document as partial rather than as
    ending where it stops.
    """
    document = make_document().model_copy(update={"content": "x" * 9000})
    graph, model = _graph_answering("Answered from the archive [1].", (document,))

    graph.invoke({"query": "What did we decide about scheduling?"})

    evidence = model.invoke.call_args.args[0][-1][1]
    assert evidence.count("x") == MAX_DOCUMENT_CHARACTERS
    assert TRUNCATION_NOTICE.strip() in evidence


def test_a_document_within_the_budget_is_sent_whole() -> None:
    """Truncation must not be the normal case; the archive is mostly short."""
    document = make_document()
    graph, model = _graph_answering("Answered from the archive [1].", (document,))

    graph.invoke({"query": "What did we decide about scheduling?"})

    evidence = model.invoke.call_args.args[0][-1][1]
    assert "Start with schedules.toml and APScheduler." in evidence
    assert TRUNCATION_NOTICE.strip() not in evidence


def test_an_archived_report_reaches_the_model_without_its_own_citations() -> None:
    """The numbers an archived answer carries collide with the ones assigned here.

    Both count from one over the same few items, so a copied number is in
    range, survives validation, and points the reader at a different document.
    A real answer on 2026-09-05 credited a claim from the 31 August report to
    `[2]`, which is that report's own Forbes citation.
    """
    archived = (
        "## Answer\n\n"
        "Solowin launched a compliance engine [1]. "
        "Over 100 firms issued a joint warning [2], [4].\n\n"
        "## Sources\n\n"
        "1. [Forbes](https://www.forbes.com/one)\n"
        "2. [Forbes](https://www.forbes.com/two)\n"
    )
    document = make_document().model_copy(update={"content": archived})
    graph, model = _graph_answering("Answered from the archive [1].", (document,))

    graph.invoke({"query": "What did the most recent report say?"})

    evidence = model.invoke.call_args.args[0][-1][1]
    assert "[1]" not in evidence
    assert "[2]" not in evidence
    assert "forbes.com" not in evidence
    assert "Solowin launched a compliance engine." in evidence
    assert "Over 100 firms issued a joint warning." in evidence


def test_a_chat_exchange_loses_its_reference_list_too() -> None:
    """Chat documents end in `Sources:`, research reports in `## Sources`."""
    archived = (
        "User:\nwhat's the weather\n\n"
        "ORIS:\nScattered clouds at 79F [1]. Visibility is 10 miles [4].\n\n"
        "Sources:\n"
        "[1] [Weather Street](https://weatherstreet.com/x)\n"
        "[4] [NWS](https://forecast.weather.gov/y)\n"
    )
    document = make_document().model_copy(update={"content": archived})
    graph, model = _graph_answering("Answered from the archive [1].", (document,))

    graph.invoke({"query": "What was the weather?"})

    evidence = model.invoke.call_args.args[0][-1][1]
    assert "weatherstreet.com" not in evidence
    assert "Scattered clouds at 79F. Visibility is 10 miles." in evidence


def test_a_document_with_no_citations_of_its_own_is_unchanged() -> None:
    """Stripping must not be a rewrite; half the archive carries no numbers."""
    document = make_document()
    graph, model = _graph_answering("Answered from the archive [1].", (document,))

    graph.invoke({"query": "What did we decide about scheduling?"})

    evidence = model.invoke.call_args.args[0][-1][1]
    assert "Start with schedules.toml and APScheduler." in evidence


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
        ("answer_from_knowledge", "validate_answer"),
        ("validate_answer", "__end__"),
    }
