"""Tests for the fixed Community Research graph."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from oris.community_research import (
    ITEM_TOKEN_BUDGET,
    ItemFindings,
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
    findings: ItemFindings | None = None,
) -> tuple[Mock, Mock, AsyncMock]:
    """Create controlled MCP-tool and model doubles.

    The model double answers every source with the same findings, because what
    these tests check is the call the graph makes and the answer it assembles,
    not what a model would say about one source rather than another.
    """
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
    structured_model.ainvoke.return_value = findings or ItemFindings(
        findings="The community discussed LangGraph.",
        cited_urls=("https://news.ycombinator.com/item?id=123",),
    )
    model.with_structured_output.return_value = structured_model
    return tool, model, structured_model


def test_community_research_describes_every_item_in_its_own_call() -> None:
    """Each returned item gets a call that saw only that item.

    Two invariants, both structural rather than asked of the model. Coverage:
    one call per item, so an item cannot be skipped. Filing: the heading its
    findings appear under is chosen by ORIS from the source that returned it,
    so an item cannot be reported under another source.
    """
    tool, model, structured_model = make_dependencies()
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result == {
        "answer": (
            "X\nQueried, and returned nothing.\n\n"
            "Hacker News\nThe community discussed LangGraph.\n\n"
            "arXiv\nQueried, and returned nothing."
        ),
        "cited_urls": ["https://news.ycombinator.com/item?id=123"],
        "research_result": make_tool_result(),
    }
    tool.ainvoke.assert_awaited_once()
    tool_call = tool.ainvoke.await_args.args[0]
    assert tool_call["args"] == {
        "topic": "LangGraph",
        "days": 7,
        "sources": ["x", "hn", "arxiv"],
        "max_results_per_source": 25,
    }
    model.with_structured_output.assert_called_once_with(
        ItemFindings,
        method="json_schema",
    )

    # One item was returned, by Hacker News, so exactly one call is made.
    structured_model.ainvoke.assert_awaited_once()
    call = structured_model.ainvoke.await_args
    assert call.kwargs == {"max_completion_tokens": ITEM_TOKEN_BUDGET}
    system, human = call.args[0]
    assert date.today().isoformat() in system[1]
    assert "Source: Hacker News" in human[1]
    assert '"source_id": "123"' in human[1]


def test_community_research_reports_a_source_error_from_the_result() -> None:
    """A failed source is named as failed, not left looking merely quiet.

    Copied from Net-Razor rather than summarised: a source that errored and a
    source that found nothing read identically otherwise, and only one of them
    means nothing was said.
    """
    failed = make_tool_result()
    failed["sources"]["x"]["errors"] = [
        {"type": "rate_limited", "message": "X search failed with HTTP 429"}
    ]
    tool, model, _ = make_dependencies(
        tool_result=ToolMessage(
            content="Net-Razor returned structured research data.",
            artifact={"structured_content": failed},
            tool_call_id="test-tool-call",
            name="net_razor_research",
        )
    )
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert "Net-Razor reported: X search failed with HTTP 429" in str(result["answer"])


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
        findings=ItemFindings(findings="The community discussed it.", cited_urls=())
    )
    graph = create_community_research_graph(tool, model)

    with pytest.raises(ValueError, match="at least one cited URL"):
        asyncio.run(graph.ainvoke({"topic": "LangGraph"}))


def test_community_research_rejects_a_url_not_supplied_by_net_razor() -> None:
    """A model cannot introduce a source URL absent from the MCP result."""
    tool, model, _ = make_dependencies(
        findings=ItemFindings(
            findings="A claim.", cited_urls=("https://example.com/invented",)
        )
    )
    graph = create_community_research_graph(tool, model)

    with pytest.raises(ValueError, match="cited unavailable URLs"):
        asyncio.run(graph.ainvoke({"topic": "LangGraph"}))


def test_community_research_rejects_a_link_written_inside_an_item() -> None:
    """A URL in an item's own text is not a citation, however relevant it looks.

    Net-Razor supplies the whole post, so a project link inside the body is
    technically "supplied" -- which is how the earlier wording read to a model
    that followed it literally, and why every one of its runs was rejected.
    Only the item's canonical_url identifies the item.
    """
    research_result = make_tool_result()
    research_result["results"]["hn"][0]["text"] = (
        "A discussion about LangGraph. Docs at https://docs.example.com"
    )
    tool_result = ToolMessage(
        content="Net-Razor returned structured research data.",
        artifact={"structured_content": research_result},
        tool_call_id="test-tool-call",
        name="net_razor_research",
    )
    tool, model, _ = make_dependencies(
        tool_result=tool_result,
        findings=ItemFindings(
            findings="A claim.", cited_urls=("https://docs.example.com",)
        ),
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
        findings=ItemFindings(
            findings="The supplied evidence is insufficient.", cited_urls=()
        ),
    )
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result["answer"] == (
        "X\nQueried, and returned nothing.\n\n"
        "Hacker News\nQueried, and returned nothing.\n\n"
        "arXiv\nQueried, and returned nothing."
    )


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
