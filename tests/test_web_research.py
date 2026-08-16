"""Tests for the deterministic Web Research graph."""

import asyncio
from datetime import date
from unittest.mock import Mock, call

import pytest
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from oris.search import (
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from oris.search_planning import SearchPlan
from oris.web_research import CitedAnswer, create_web_research_graph


class FakeWebSearch:
    """In-memory search implementation used through the production interface."""

    def __init__(self) -> None:
        self.requests: list[WebSearchRequest] = []

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        self.requests.append(request)
        return WebSearchResponse(
            query=request.query,
            results=(
                WebSearchResult(
                    title="LangGraph overview",
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    snippet="LangGraph supports stateful agent workflows.",
                    relevance_score=0.95,
                ),
            ),
            provider="fake-search",
            provider_request_id="fake-request-1",
        )


def create_fake_model(
    answer: CitedAnswer,
    plan: SearchPlan | None = None,
) -> tuple[Mock, Mock, Mock]:
    """Return a model double with controlled planning and answer responses."""
    answer_model = Mock()
    answer_model.invoke.return_value = answer
    planning_model = Mock()
    planning_model.invoke.return_value = plan or SearchPlan(
        search_query="LangGraph architecture"
    )
    model = Mock(spec=ChatOpenAI)

    def select_structured_model(schema: type, *, method: str) -> Mock:
        assert method == "json_schema"
        if schema is CitedAnswer:
            return answer_model
        if schema is SearchPlan:
            return planning_model
        raise AssertionError(f"Unexpected structured-output schema: {schema}")

    model.with_structured_output.side_effect = select_structured_model
    return model, planning_model, answer_model


def test_web_research_validates_searches_and_synthesizes_once() -> None:
    """A valid invocation follows the fixed path and returns a cited answer."""
    search = FakeWebSearch()
    expected_answer = CitedAnswer(
        answer="LangGraph supports stateful agent workflows [1]."
    )
    model, planning_model, answer_model = create_fake_model(expected_answer)
    graph = create_web_research_graph(search, model)

    result = asyncio.run(
        graph.ainvoke({"query": "  What is LangGraph's architecture?  "})
    )

    assert search.requests == [WebSearchRequest(query="LangGraph architecture")]
    assert result == {
        "answer": expected_answer,
        "sources": (
            WebSearchResult(
                title="LangGraph overview",
                url="https://docs.langchain.com/oss/python/langgraph/overview",
                snippet="LangGraph supports stateful agent workflows.",
                relevance_score=0.95,
            ),
        ),
    }
    assert model.with_structured_output.call_args_list == [
        call(CitedAnswer, method="json_schema"),
        call(SearchPlan, method="json_schema"),
    ]
    planning_model.invoke.assert_called_once()
    answer_model.invoke.assert_called_once()

    messages = answer_model.invoke.call_args.args[0]
    assert messages[0][0] == "system"
    assert messages[1][0] == "human"
    assert "Question:\nWhat is LangGraph's architecture?" in messages[1][1]
    assert "Executed search request:" in messages[1][1]
    assert '"source_number": 1' in messages[1][1]
    assert "LangGraph overview" in messages[1][1]
    assert "https://docs.langchain.com/oss/python/langgraph/overview" in messages[1][1]
    assert answer_model.invoke.call_args.kwargs == {"max_completion_tokens": 512}


def test_web_research_rejects_blank_input_before_searching() -> None:
    """Validation failure prevents the external search capability from running."""
    search = FakeWebSearch()
    model, planning_model, answer_model = create_fake_model(
        CitedAnswer(answer="Unused [1].")
    )
    graph = create_web_research_graph(search, model)

    with pytest.raises(ValidationError):
        asyncio.run(graph.ainvoke({"query": "   "}))

    assert search.requests == []
    planning_model.invoke.assert_not_called()
    answer_model.invoke.assert_not_called()


def test_web_research_forwards_explicit_search_controls() -> None:
    """The graph validates caller-supplied controls before searching."""
    search = FakeWebSearch()
    plan = SearchPlan(
        search_query="AI-agent developments 2026-08-07",
        time_range="year",
    )
    model, _, _ = create_fake_model(
        CitedAnswer(answer="A supported answer [1]."),
        plan,
    )
    graph = create_web_research_graph(search, model)

    asyncio.run(
        graph.ainvoke(
            {
                "query": "AI-agent developments from yesterday",
                "include_domains": ["example.com"],
                "search_category": "news",
                "start_date": date(2026, 8, 7),
                "end_date": date(2026, 8, 8),
            }
        )
    )

    assert search.requests == [
        WebSearchRequest(
            query="AI-agent developments 2026-08-07",
            include_domains=("example.com",),
            search_category="news",
            start_date=date(2026, 8, 7),
            end_date=date(2026, 8, 8),
        )
    ]


def test_web_research_uses_the_planned_search_category() -> None:
    """A planned category is used when the caller supplies no override."""
    search = FakeWebSearch()
    plan = SearchPlan(
        search_query="AI-agent news published 2026-08-08",
        search_category="news",
        start_date=date(2026, 8, 8),
        end_date=date(2026, 8, 9),
    )
    model, _, _ = create_fake_model(
        CitedAnswer(answer="A dated AI-agent report [1]."),
        plan,
    )
    graph = create_web_research_graph(search, model)

    asyncio.run(graph.ainvoke({"query": "What AI-agent news was published yesterday?"}))

    assert search.requests == [
        WebSearchRequest(
            query="AI-agent news published 2026-08-08",
            search_category="news",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 9),
        )
    ]


def test_web_research_rejects_an_answer_without_citations() -> None:
    """The graph does not return a model answer without a source citation."""
    model, _, _ = create_fake_model(CitedAnswer(answer="An uncited answer."))
    graph = create_web_research_graph(FakeWebSearch(), model)

    with pytest.raises(ValueError, match="at least one citation"):
        asyncio.run(graph.ainvoke({"query": "LangGraph architecture"}))


def test_web_research_rejects_a_citation_without_a_source() -> None:
    """The graph rejects citation numbers outside the supplied results."""
    model, _, _ = create_fake_model(CitedAnswer(answer="An invalid citation [2]."))
    graph = create_web_research_graph(FakeWebSearch(), model)

    with pytest.raises(ValueError, match=r"unavailable sources: \[2\]"):
        asyncio.run(graph.ainvoke({"query": "LangGraph architecture"}))


def test_web_research_has_only_the_approved_path() -> None:
    """The graph contains no routing branches or tool-calling loop."""
    model, _, _ = create_fake_model(CitedAnswer(answer="Unused [1]."))
    graph = create_web_research_graph(FakeWebSearch(), model)

    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

    assert edges == {
        ("__start__", "validate_request"),
        ("validate_request", "plan_search"),
        ("plan_search", "search_web"),
        ("search_web", "synthesize_answer"),
        ("synthesize_answer", "validate_answer"),
        ("validate_answer", "__end__"),
    }
