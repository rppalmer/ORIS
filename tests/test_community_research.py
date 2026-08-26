"""Tests for the fixed Community Research graph."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from oris.community_research import (
    CommunityResearchAnswer,
    create_community_research_graph,
)


def make_tool_result() -> dict:
    """Return a small JSON result shaped like Net-Razor research output."""
    return {
        "call_id": "net-razor-call-1",
        "topic": "LangGraph",
        "window": {
            "since": "2026-08-07T00:00:00Z",
            "until": "2026-08-08T00:00:00Z",
        },
        "sources": {
            "x": {"queried": True, "items_found": 0, "errors": []},
            "hn": {"queried": True, "items_found": 1, "errors": []},
        },
        "results": {
            "x": [],
            "hn": [
                {
                    "source": "hn",
                    "source_id": "123",
                    "canonical_url": "https://news.ycombinator.com/item?id=123",
                    "text": "A discussion about LangGraph.",
                }
            ],
        },
        "caveats": [],
    }


def make_dependencies(
    *,
    tool_result: ToolMessage | None = None,
    answer: CommunityResearchAnswer | None = None,
) -> tuple[Mock, Mock, AsyncMock]:
    """Create controlled MCP-tool and model doubles."""
    research_result = make_tool_result()
    tool = Mock(spec=BaseTool)
    tool.name = "net_razor_research"
    tool.ainvoke = AsyncMock(
        return_value=tool_result
        or ToolMessage(
            content="Net-Razor returned structured research data.",
            artifact={"structured_content": research_result},
            tool_call_id="test-tool-call",
            name="net_razor_research",
        )
    )
    model = Mock(spec=BaseChatModel)
    structured_model = AsyncMock()
    structured_model.ainvoke.return_value = answer or CommunityResearchAnswer(
        answer="The community discussed LangGraph.",
        cited_urls=("https://news.ycombinator.com/item?id=123",),
    )
    model.with_structured_output.return_value = structured_model
    return tool, model, structured_model


def test_community_research_calls_one_tool_and_synthesizes_once() -> None:
    """One request follows the fixed MCP-call and synthesis path."""
    tool, model, structured_model = make_dependencies()
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result == {
        "answer": "The community discussed LangGraph.",
        "cited_urls": ["https://news.ycombinator.com/item?id=123"],
        "research_result": make_tool_result(),
    }
    tool.ainvoke.assert_awaited_once()
    tool_call = tool.ainvoke.await_args.args[0]
    assert tool_call["type"] == "tool_call"
    assert tool_call["name"] == "net_razor_research"
    assert tool_call["args"] == {
        "topic": "LangGraph",
        "days": 1,
        "sources": ["x", "hn"],
        "max_results_per_source": 10,
    }
    model.with_structured_output.assert_called_once_with(
        CommunityResearchAnswer,
        method="json_schema",
    )
    structured_model.ainvoke.assert_awaited_once()
    messages = structured_model.ainvoke.await_args.args[0]
    assert messages[0][0] == "system"
    assert date.today().isoformat() in messages[0][1]
    assert messages[1][0] == "human"
    assert '"call_id": "net-razor-call-1"' in messages[1][1]
    assert "https://news.ycombinator.com/item?id=123" in messages[1][1]
    assert structured_model.ainvoke.await_args.kwargs == {"max_completion_tokens": 512}


def test_community_research_rejects_an_unapproved_source() -> None:
    """Podcast tools cannot enter the X-and-Hacker-News specialist."""
    tool, model, structured_model = make_dependencies()
    graph = create_community_research_graph(tool, model)

    with pytest.raises(ValueError, match="Unsupported Community Research sources"):
        asyncio.run(graph.ainvoke({"topic": "LangGraph", "sources": ["yt"]}))

    tool.ainvoke.assert_not_awaited()
    structured_model.ainvoke.assert_not_awaited()


def test_community_research_requires_structured_json() -> None:
    """Text-only MCP output cannot silently replace the JSON research result."""
    tool, model, structured_model = make_dependencies(
        tool_result=ToolMessage(
            content="Text only",
            tool_call_id="test-tool-call",
            name="net_razor_research",
        )
    )
    graph = create_community_research_graph(tool, model)

    with pytest.raises(ValueError, match="structured JSON"):
        asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    structured_model.ainvoke.assert_not_awaited()


def test_community_research_requires_a_citation_when_evidence_exists() -> None:
    """An evidence-backed answer must contain at least one Markdown link."""
    tool, model, _ = make_dependencies(
        answer=CommunityResearchAnswer(
            answer="The community discussed LangGraph.",
            cited_urls=(),
        )
    )
    graph = create_community_research_graph(tool, model)

    with pytest.raises(ValueError, match="at least one cited URL"):
        asyncio.run(graph.ainvoke({"topic": "LangGraph"}))


def test_community_research_rejects_a_url_not_supplied_by_net_razor() -> None:
    """A model cannot introduce a source URL absent from the MCP result."""
    tool, model, _ = make_dependencies(
        answer=CommunityResearchAnswer(
            answer="A claim.",
            cited_urls=("https://example.com/invented",),
        )
    )
    graph = create_community_research_graph(tool, model)

    with pytest.raises(ValueError, match="cited unavailable URLs"):
        asyncio.run(graph.ainvoke({"topic": "LangGraph"}))


def test_community_research_allows_no_citation_when_no_evidence_exists() -> None:
    """An honest insufficient-evidence response does not invent a citation."""
    empty_result = make_tool_result()
    empty_result["results"] = {"x": [], "hn": []}
    tool, model, _ = make_dependencies(
        tool_result=ToolMessage(
            content="Net-Razor returned no results.",
            artifact={"structured_content": empty_result},
            tool_call_id="test-tool-call",
            name="net_razor_research",
        ),
        answer=CommunityResearchAnswer(
            answer="The supplied evidence is insufficient.",
            cited_urls=(),
        ),
    )
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result["answer"] == "The supplied evidence is insufficient."


def test_community_research_has_only_the_approved_path() -> None:
    """The specialist has no router or model-controlled tool loop."""
    tool, model, _ = make_dependencies()
    graph = create_community_research_graph(tool, model)

    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

    assert edges == {
        ("__start__", "validate_request"),
        ("validate_request", "collect_evidence"),
        ("collect_evidence", "synthesize_answer"),
        ("synthesize_answer", "validate_citations"),
        ("validate_citations", "__end__"),
    }
