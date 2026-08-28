"""Fixed community-research workflow backed by Net-Razor MCP."""

import asyncio
import json
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.net_razor import COMMUNITY_RESEARCH_TOOL_NAMES
from oris.prompts import load_system_prompt, with_current_date
from oris.search import NonEmptyText

COMMUNITY_RESEARCH_SYSTEM_PROMPT = load_system_prompt("community_research_system.txt")
ITEM_TOKEN_BUDGET = 512
"""Completion tokens for one item's description.

An item gets two or three sentences, about 80 tokens, plus its own
`canonical_url` in `cited_urls` at roughly 25 more. 512 is far above that on
purpose: the cost of overshooting is not a short answer but a dead specialist
or prose cut off mid-sentence, and at this size the headroom is nearly free.

The budget used to cover a whole fan-out and was raised twice chasing the same
failure. Sized per item it stops moving, because it no longer depends on how
many sources were asked for or how much each returned.
"""

COMMUNITY_SOURCES = ("x", "hn", "arxiv")
"""The Net-Razor sources a community topic fans out to.

arXiv joined on 2026-08-27. It was available in Net-Razor from the start and
simply never asked for, so a question about recent papers fell through to Web
Research and came back as a Tavily summary of pages about arXiv rather than the
preprints themselves.

The one-day window does not apply to it. arXiv announces on weekdays only, so
Net-Razor widens that leg to seven days itself and echoes the effective window
back per source. ORIS does not restate the number.
"""

SOURCE_LABELS = {"x": "X", "hn": "Hacker News", "arxiv": "arXiv"}
"""How each source is titled in the assembled answer.

The heading is written here rather than asked of the model. A source's name is
a fixed fact about the fan-out, and the run that made this file necessary spent
its whole budget on formatting instructions it then narrated back.
"""


class ItemFindings(BaseModel):
    """What one returned item carried, written by a call that saw only it.

    One call per item rather than one per source, because a call handed a list
    summarised the first entry and stopped. Four prompt wordings were measured
    against ten arXiv papers — asking for a bullet each, forbidding an early
    stop, setting sentences per item, and numbering the items in the evidence
    — and every one returned a single paper. A call given one item has no list
    to stop partway through, so coverage stops being a choice the model makes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: NonEmptyText = Field(
        description="What this item carried, in two or three sentences, without URLs."
    )
    cited_urls: tuple[NonEmptyText, ...] = Field(
        description=(
            "This item's canonical_url if its findings rest on it. Use only URLs "
            "supplied in the Net-Razor result."
        )
    )


class CommunityResearchInput(TypedDict):
    """Public input accepted by the Community Research graph."""

    topic: str
    days: NotRequired[int]
    sources: NotRequired[list[Literal["x", "hn", "arxiv"]]]
    max_results_per_source: NotRequired[int]


class CommunityResearchOutput(TypedDict):
    """Public JSON-compatible output returned by Community Research."""

    answer: str
    cited_urls: list[str]
    research_result: dict[str, Any]


class CommunityResearchState(TypedDict):
    """Internal state shared by Community Research nodes."""

    topic: str
    days: NotRequired[int]
    sources: NotRequired[list[Literal["x", "hn", "arxiv"]]]
    max_results_per_source: NotRequired[int]
    research_result: NotRequired[dict[str, Any]]
    answer: NotRequired[str]
    cited_urls: NotRequired[list[str]]


def _canonical_urls(research_result: dict[str, Any]) -> set[str]:
    """Collect evidence URLs from Net-Razor's grouped results."""
    grouped_results = research_result.get("results")
    if not isinstance(grouped_results, dict):
        return set()

    urls: set[str] = set()
    for source_results in grouped_results.values():
        if not isinstance(source_results, list):
            continue
        for item in source_results:
            if not isinstance(item, dict):
                continue
            url = item.get("canonical_url")
            if isinstance(url, str) and url:
                urls.add(url)
    return urls


def _reported_errors(report: object) -> list[str]:
    """Read the errors Net-Razor recorded for one source.

    Rendered from the result rather than asked of a model. An error is the one
    part of a fan-out a reader must be able to trust completely: a source that
    failed and a source that found nothing look identical in the answer
    otherwise, and only one of them means "nothing was said".
    """
    if not isinstance(report, dict):
        return []
    errors = report.get("errors")
    if not isinstance(errors, list):
        return []
    messages: list[str] = []
    for error in errors:
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if isinstance(message, str) and message:
                messages.append(message)
        elif isinstance(error, str) and error:
            messages.append(error)
    return messages


def create_community_research_graph(
    research_tool: BaseTool,
    model: BaseChatModel,
) -> CompiledStateGraph:
    """Compile a fixed workflow around the one approved Net-Razor tool."""
    expected_tool_name = COMMUNITY_RESEARCH_TOOL_NAMES[0]
    if research_tool.name != expected_tool_name:
        raise ValueError(
            f"Community Research requires {expected_tool_name}, "
            f"not {research_tool.name}"
        )
    structured_model = model.with_structured_output(
        ItemFindings,
        method="json_schema",
    )

    def validate_request(state: CommunityResearchState) -> dict[str, object]:
        sources = state.get("sources", list(COMMUNITY_SOURCES))
        if not sources:
            raise ValueError("Community Research requires at least one source")
        unsupported_sources = sorted(set(sources) - set(COMMUNITY_SOURCES))
        if unsupported_sources:
            raise ValueError(
                f"Unsupported Community Research sources: {unsupported_sources}"
            )

        return {
            "days": state.get("days", 1),
            "sources": sources,
            "max_results_per_source": state.get("max_results_per_source", 10),
        }

    async def collect_evidence(
        state: CommunityResearchState,
    ) -> dict[str, dict[str, Any]]:
        result = await research_tool.ainvoke(
            {
                "type": "tool_call",
                "id": str(uuid4()),
                "name": research_tool.name,
                "args": {
                    "topic": state["topic"],
                    "days": state["days"],
                    "sources": state["sources"],
                    "max_results_per_source": state["max_results_per_source"],
                },
            }
        )
        if not isinstance(result, ToolMessage):
            raise TypeError("Net-Razor did not return a LangChain ToolMessage")
        if not isinstance(result.artifact, dict):
            raise ValueError("Net-Razor did not return structured JSON")
        structured_content = result.artifact.get("structured_content")
        if not isinstance(structured_content, dict):
            raise ValueError("Net-Razor did not return structured JSON")
        return {"research_result": structured_content}

    async def synthesize_answer(
        state: CommunityResearchState,
    ) -> dict[str, object]:
        """Describe every returned item, then assemble the answer per source.

        Two things used to be asked of one model call and are now structural.
        Filing: a single call reading all three sources put every arXiv paper
        inside X's section, though each item carries its own `source` field.
        Coverage: a call given one source's ten papers described the first and
        stopped, under four different prompt wordings.

        Both were the model choosing, so both are removed rather than argued
        with. A call sees one item, and where its findings appear is decided
        here. What a source returned nothing about, and what errors it
        reported, is copied from Net-Razor rather than summarised, because
        those are facts about the fan-out that no model needs to restate.

        The calls run concurrently because they share nothing.
        """
        research_result = state["research_result"]
        grouped = research_result.get("results", {})
        reported = research_result.get("sources", {})

        async def describe(source: str, item: object) -> tuple[str, ItemFindings]:
            findings = await structured_model.ainvoke(
                [
                    ("system", with_current_date(COMMUNITY_RESEARCH_SYSTEM_PROMPT)),
                    (
                        "human",
                        f"Research topic:\n{state['topic']}\n\n"
                        f"Source: {SOURCE_LABELS.get(source, source)}\n\n"
                        "One item returned by that source:\n"
                        f"{json.dumps(item, ensure_ascii=False, indent=2)}",
                    ),
                ],
                max_completion_tokens=ITEM_TOKEN_BUDGET,
            )
            return source, findings

        jobs = [
            (source, item)
            for source in state["sources"]
            for item in grouped.get(source, [])
            if isinstance(item, dict)
        ]
        described = await asyncio.gather(
            *(describe(source, item) for source, item in jobs)
        )

        by_source: dict[str, list[ItemFindings]] = {
            source: [] for source in state["sources"]
        }
        for source, findings in described:
            by_source[source].append(findings)

        blocks: list[str] = []
        cited_urls: list[str] = []
        for source in state["sources"]:
            entries = by_source[source]
            # Joined into a paragraph rather than a bullet per item. The items
            # are described one at a time because that is the only way this
            # model keeps their specifics, but that is a fact about how the
            # answer is produced, and a reader should not have to see it.
            summary = " ".join(entry.findings.strip() for entry in entries)
            if not summary:
                summary = "Queried, and returned nothing."
            errors = _reported_errors(reported.get(source))
            if errors:
                summary = f"{summary} Net-Razor reported: {'; '.join(errors)}."
            blocks.append(f"{SOURCE_LABELS.get(source, source)}\n{summary}")
            for entry in entries:
                for url in entry.cited_urls:
                    if url not in cited_urls:
                        cited_urls.append(url)

        return {"answer": "\n\n".join(blocks), "cited_urls": cited_urls}

    def validate_citations(state: CommunityResearchState) -> dict:
        available_urls = _canonical_urls(state["research_result"])
        cited_urls = set(state["cited_urls"])
        unsupported_urls = sorted(cited_urls - available_urls)
        if unsupported_urls:
            raise ValueError(
                f"The community research answer cited unavailable URLs: "
                f"{unsupported_urls}"
            )

        if available_urls and not cited_urls:
            raise ValueError(
                "The community research answer must include at least one cited URL"
            )
        return {}

    builder = StateGraph(
        CommunityResearchState,
        input_schema=CommunityResearchInput,
        output_schema=CommunityResearchOutput,
    )
    builder.add_node("validate_request", validate_request)
    builder.add_node("collect_evidence", collect_evidence)
    builder.add_node("synthesize_answer", synthesize_answer)
    builder.add_node("validate_citations", validate_citations)
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "collect_evidence")
    builder.add_edge("collect_evidence", "synthesize_answer")
    builder.add_edge("synthesize_answer", "validate_citations")
    builder.add_edge("validate_citations", END)
    return builder.compile()
