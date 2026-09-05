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
        findings="The community discussed LangGraph.", bears_on_topic=True
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


def test_community_research_answers_when_no_item_bore_on_the_topic() -> None:
    """Nobody discussing a subject is the answer, not a failure to answer.

    The model never writes a URL here: it flags each item and ORIS collects the
    canonical URLs of the flagged ones. So an empty citation list means every
    item was judged off topic, which the prompt asks for. Requiring a citation
    made an obscure topic raise instead of reporting the silence.
    """
    tool, model, _ = make_dependencies(
        findings=ItemFindings(
            findings="Nothing here bore on the topic.", bears_on_topic=False
        )
    )
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result["cited_urls"] == []
    assert "none of which discussed the topic" in result["answer"]
    # Said once for the source, not once per item.
    assert "Nothing here bore on the topic." not in result["answer"]


def test_community_research_cites_the_item_it_was_given() -> None:
    """The citation comes from the evidence, never from the model.

    Qwen3.5 asked to retype a 19-digit X status id kept the handle and dropped
    digits out of the middle, and the whole answer was then rejected for citing
    a URL nobody supplied -- one run in three on an X-heavy topic. The model is
    no longer asked for something the calling code already holds.
    """
    tool, model, _ = make_dependencies(
        findings=ItemFindings(findings="A claim about LangGraph.", bears_on_topic=True)
    )
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result["cited_urls"] == ["https://news.ycombinator.com/item?id=123"]


def test_an_off_topic_item_is_counted_rather_than_described() -> None:
    """The model's own relevance call decides what reaches the answer.

    Every item carries the model's judgement on whether it bore on the topic.
    This used to describe all of them anyway, so a real answer about an obscure
    subject ran to roughly 1,200 words, most of it reporting that Sanskrit job
    postings and luxury-brand statistics did not mention it. The count is kept
    so a source returning two useful items does not read like one that returned
    two items in total.
    """
    research_result = make_tool_result()
    research_result["results"]["hn"].append(
        {
            "source": "hn",
            "source_id": "456",
            "canonical_url": "https://news.ycombinator.com/item?id=456",
            "text": "An unrelated discussion.",
        }
    )
    tool_result = ToolMessage(
        content="Net-Razor returned structured research data.",
        artifact={"structured_content": research_result},
        tool_call_id="test-tool-call",
        name="net_razor_research",
    )
    tool, model, structured_model = make_dependencies(tool_result=tool_result)
    structured_model.ainvoke.side_effect = [
        ItemFindings(findings="A claim about LangGraph.", bears_on_topic=True),
        ItemFindings(findings="This said nothing about it.", bears_on_topic=False),
    ]
    graph = create_community_research_graph(tool, model)

    result = asyncio.run(graph.ainvoke({"topic": "LangGraph"}))

    assert result["cited_urls"] == ["https://news.ycombinator.com/item?id=123"]
    assert "A claim about LangGraph." in result["answer"]
    assert "This said nothing about it." not in result["answer"]
    assert "1 further item was returned" in result["answer"]


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
            findings="The supplied evidence is insufficient.",
            bears_on_topic=False,
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
