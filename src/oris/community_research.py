"""Fixed community-research workflow backed by Net-Razor MCP."""

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
COMMUNITY_SOURCES = ("x", "hn")


class CommunityResearchAnswer(BaseModel):
    """Structured local-model response for Community Research."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: NonEmptyText = Field(description="Concise answer text without source URLs.")
    cited_urls: tuple[NonEmptyText, ...] = Field(
        description=(
            "Canonical evidence URLs supporting the answer. Use only URLs supplied "
            "in the Net-Razor result."
        )
    )


class CommunityResearchInput(TypedDict):
    """Public input accepted by the Community Research graph."""

    topic: str
    days: NotRequired[int]
    sources: NotRequired[list[Literal["x", "hn"]]]
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
    sources: NotRequired[list[Literal["x", "hn"]]]
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
        CommunityResearchAnswer,
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
    ) -> dict[str, str]:
        response = await structured_model.ainvoke(
            [
                ("system", with_current_date(COMMUNITY_RESEARCH_SYSTEM_PROMPT)),
                (
                    "human",
                    f"Research topic:\n{state['topic']}\n\n"
                    "Net-Razor result:\n"
                    f"{json.dumps(state['research_result'], ensure_ascii=False, indent=2)}",
                ),
            ],
            max_completion_tokens=1024,
        )
        return {
            "answer": response.answer,
            "cited_urls": list(response.cited_urls),
        }

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
