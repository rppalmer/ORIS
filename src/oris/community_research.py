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

An item gets two or three sentences, about 80 tokens, plus a boolean. 512 is
far above that on purpose: the cost of overshooting is not a short answer but a
dead specialist or prose cut off mid-sentence, and at this size the headroom is
nearly free.

The budget used to cover a whole fan-out and was raised twice chasing the same
failure. Sized per item it stops moving, because it no longer depends on how
many sources were asked for or how much each returned.
"""

MAX_CONCURRENT_ITEM_CALLS = 8
"""How many item descriptions may be in flight at once.

oMLX rejects work past a fixed waiting queue: "Scheduler waiting queue full
(32/32)". A week of three sources at twenty-five results each is fifty-eight
items, and firing them together returned 503 and killed the run. The server
owns that limit and is right to enforce it; what ORIS owns is not exceeding it.

Eight rather than thirty-one because the server is shared and raising it buys
almost nothing. A `/community` run must not fill the queue that the interactive
chat, a scheduled report, or a Threat Intel lookup is waiting in. Measured on
the same fifty-eight items: eight at a time took 350 seconds, sixteen took 319.
Nine per cent, for double the footprint. The limit is how fast the model
generates, not how many calls are queued behind it.

This is an ORIS-owned limit, not a Net-Razor one. It bounds model calls in one
graph run, which no MCP contract expresses.
"""

DEFAULT_RESEARCH_DAYS = 7
"""How far back a community topic looks, in days.

Raised from one on 2026-08-28. A single day starved Hacker News: every run
across a day of testing returned nought to two items from it, against ten from
X, because a niche technical topic does not produce a Hacker News story every
twenty-four hours. arXiv never had the problem, and only because Net-Razor
widens that leg to a week itself — which is the same correction, already made
once, for the same reason.

Net-Razor allows up to 3,650. Seven is a week of reading rather than a limit
found by experiment, and the number ORIS asks for is deliberately the same one
Net-Razor already forces on arXiv.
"""

DEFAULT_RESULTS_PER_SOURCE = 25
"""How many items each source is asked for.

Raised from ten, which was below Net-Razor's own default of twenty-five and
appears to have been chosen before anything measured it. Fifty is the ceiling
the provider allows.

Each item is described by its own model call, so this sets how long a run takes
much more directly than it sets how much is read. It is the number to lower if
runs feel slow, and lowering it costs coverage rather than quality: the items
that are covered are written up exactly the same way.
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
    bears_on_topic: bool = Field(
        description=(
            "True if this item said something about the research topic. False "
            "if the findings only record that it did not."
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
            "days": state.get("days", DEFAULT_RESEARCH_DAYS),
            "sources": sources,
            "max_results_per_source": state.get(
                "max_results_per_source", DEFAULT_RESULTS_PER_SOURCE
            ),
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
        in_flight = asyncio.Semaphore(MAX_CONCURRENT_ITEM_CALLS)

        async def describe_when_free(
            source: str, item: object
        ) -> tuple[str, ItemFindings]:
            async with in_flight:
                return await describe(source, item)

        described = await asyncio.gather(
            *(describe_when_free(source, item) for source, item in jobs)
        )

        by_source: dict[str, list[tuple[dict, ItemFindings]]] = {
            source: [] for source in state["sources"]
        }
        for (source, item), (_, findings) in zip(jobs, described, strict=True):
            by_source[source].append((item, findings))

        blocks: list[str] = []
        cited_urls: list[str] = []
        for source in state["sources"]:
            entries = by_source[source]
            # Joined into a paragraph rather than a bullet per item. The items
            # are described one at a time because that is the only way this
            # model keeps their specifics, but that is a fact about how the
            # answer is produced, and a reader should not have to see it.
            if not entries:
                summary = "Queried, and returned nothing."
            elif not any(findings.bears_on_topic for _, findings in entries):
                # Every item was judged off topic, which is an answer rather
                # than a failure. Said once, because the alternative is a
                # paragraph of per-item sentences each reporting the same
                # nothing.
                noun = "item" if len(entries) == 1 else "items"
                summary = (
                    f"Queried, and returned {len(entries)} {noun}, "
                    "none of which discussed the topic."
                )
            else:
                # Use the judgement that was asked for. Every item carries the
                # model's own call on whether it bore on the topic, and this
                # used to print all of them regardless, so an answer about
                # LangGraph carried paragraphs on Sanskrit job postings because
                # the search returned them. The model was not failing to
                # filter; its filtering was being discarded here.
                relevant = [f for _, f in entries if f.bears_on_topic]
                summary = " ".join(f.findings.strip() for f in relevant)
                skipped = len(entries) - len(relevant)
                if skipped:
                    # Said, so a source that returned two useful items does not
                    # read the same as one that returned two items in total.
                    said = "item was" if skipped == 1 else "items were"
                    summary = (
                        f"{summary} {skipped} further {said} returned "
                        "and did not discuss the topic."
                    )
            errors = _reported_errors(reported.get(source))
            if errors:
                summary = f"{summary} Net-Razor reported: {'; '.join(errors)}."
            blocks.append(f"{SOURCE_LABELS.get(source, source)}\n{summary}")
            # The citation is the item this call was given, taken from the
            # evidence rather than retyped by the model. Asking for it back cost
            # a run in three on X-heavy topics: Qwen3.5 keeps the handle and
            # drops digits out of the middle of a 19-digit status id, and the
            # answer is then rejected whole for citing a URL nobody supplied.
            for item, findings in entries:
                url = item.get("canonical_url")
                if findings.bears_on_topic and url and url not in cited_urls:
                    cited_urls.append(url)

        return {"answer": "\n\n".join(blocks), "cited_urls": cited_urls}

    def validate_citations(state: CommunityResearchState) -> dict:
        """Reject a URL nobody supplied. Do not require that any exist.

        Web Research demands at least one citation because its model writes the
        prose and could write it uncited, and an uncited claim there cannot be
        checked at all. This specialist has no such failure available to it:
        the model never types a URL. It sets one flag per item saying whether
        that item bore on the topic, and ORIS then takes the canonical URL of
        each flagged item straight out of the evidence.

        So an empty citation list here does not mean an uncited answer. It
        means the model judged every returned item irrelevant, which the prompt
        explicitly asks it to do. Requiring a citation anyway asserted that the
        search had found something relevant -- a claim about the world rather
        than about the answer, and not one ORIS can make. It cost a real run:
        `obscure-topic` raised instead of reporting that nobody is discussing
        the subject. That is the answer to the question, not a failure to
        answer it.

        What remains worth checking is the reverse. Nothing today can cite a
        URL that was not supplied, because the citations are assembled rather
        than written; if that ever changes back, this is what catches it.
        """
        available_urls = _canonical_urls(state["research_result"])
        unsupported_urls = sorted(set(state["cited_urls"]) - available_urls)
        if unsupported_urls:
            raise ValueError(
                f"The community research answer cited unavailable URLs: "
                f"{unsupported_urls}"
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
