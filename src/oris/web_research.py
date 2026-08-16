"""The deterministic Web Research LangGraph workflow."""

import json
import re
from datetime import date
from typing import NotRequired, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.prompts import load_system_prompt, with_current_date
from oris.search import (
    DomainName,
    NonEmptyText,
    SearchCategory,
    SearchTimeRange,
    WebSearch,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from oris.search_planning import create_search_plan

WEB_RESEARCH_SYSTEM_PROMPT = load_system_prompt("web_research_system.txt")


class CitedAnswer(BaseModel):
    """An answer whose citations refer to the supplied search results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: NonEmptyText = Field(
        description="Answer with factual claims cited inline as [source number]."
    )


class WebResearchInput(TypedDict):
    """Public input accepted by the Web Research graph."""

    query: str
    include_domains: NotRequired[list[str]]
    search_category: NotRequired[SearchCategory]
    time_range: NotRequired[SearchTimeRange]
    start_date: NotRequired[date]
    end_date: NotRequired[date]


class WebResearchOutput(TypedDict):
    """Public output returned by the Web Research graph."""

    answer: CitedAnswer
    sources: tuple[WebSearchResult, ...]


class WebResearchState(TypedDict):
    """Internal state shared by Web Research nodes."""

    query: str
    include_domains: NotRequired[tuple[DomainName, ...]]
    search_category: NotRequired[SearchCategory]
    time_range: NotRequired[SearchTimeRange | None]
    start_date: NotRequired[date | None]
    end_date: NotRequired[date | None]
    search_request: NotRequired[WebSearchRequest]
    search_response: NotRequired[WebSearchResponse]
    answer: NotRequired[CitedAnswer]
    sources: NotRequired[tuple[WebSearchResult, ...]]


def validate_request(state: WebResearchInput) -> dict[str, object]:
    """Validate and normalize the user's query before external access."""
    request = WebSearchRequest(
        query=state["query"],
        include_domains=state.get("include_domains", []),
        search_category=state.get("search_category", "general"),
        time_range=state.get("time_range"),
        start_date=state.get("start_date"),
        end_date=state.get("end_date"),
    )
    validated_state: dict[str, object] = {
        "query": request.query,
        "include_domains": request.include_domains,
        "time_range": request.time_range,
        "start_date": request.start_date,
        "end_date": request.end_date,
    }
    if "search_category" in state:
        validated_state["search_category"] = request.search_category
    return validated_state


def _format_evidence(results: tuple[WebSearchResult, ...]) -> str:
    """Serialize normalized results with stable, one-based source numbers."""
    evidence = [
        {
            "source_number": source_number,
            "title": result.title,
            "url": str(result.url),
            "snippet": result.snippet,
            "published_at": (
                result.published_at.isoformat()
                if result.published_at is not None
                else None
            ),
        }
        for source_number, result in enumerate(results, start=1)
    ]
    return json.dumps(evidence, ensure_ascii=False, indent=2)


def create_web_research_graph(
    search: WebSearch,
    model: ChatOpenAI,
) -> CompiledStateGraph:
    """Compile the Web Research graph with its injected dependencies."""
    structured_model = model.with_structured_output(
        CitedAnswer,
        method="json_schema",
    )

    async def search_web(state: WebResearchState) -> dict[str, WebSearchResponse]:
        return {"search_response": await search.search(state["search_request"])}

    def plan_search(state: WebResearchState) -> dict[str, WebSearchRequest]:
        plan = create_search_plan(model, state["query"], current_date=date.today())
        if state.get("start_date") is not None:
            time_range = None
            start_date = state["start_date"]
            end_date = state["end_date"]
        elif state.get("time_range") is not None:
            time_range = state["time_range"]
            start_date = None
            end_date = None
        else:
            time_range = plan.time_range
            start_date = plan.start_date
            end_date = plan.end_date

        return {
            "search_request": WebSearchRequest(
                query=plan.search_query,
                include_domains=state.get("include_domains") or plan.include_domains,
                search_category=state.get("search_category", plan.search_category),
                time_range=time_range,
                start_date=start_date,
                end_date=end_date,
            )
        }

    def synthesize_answer(
        state: WebResearchState,
    ) -> dict[str, object]:
        search_response = state["search_response"]
        answer = structured_model.invoke(
            [
                (
                    "system",
                    with_current_date(WEB_RESEARCH_SYSTEM_PROMPT),
                ),
                (
                    "human",
                    f"Question:\n{state['query']}\n\n"
                    "Executed search request:\n"
                    f"{state['search_request'].model_dump_json(indent=2)}\n\n"
                    f"Evidence:\n{_format_evidence(search_response.results)}",
                ),
            ],
            max_completion_tokens=512,
        )
        return {
            "answer": answer,
            "sources": search_response.results,
        }

    def validate_answer(state: WebResearchState) -> dict[str, object]:
        citation_numbers = [
            int(number) for number in re.findall(r"\[(\d+)\]", state["answer"].answer)
        ]
        if not citation_numbers:
            raise ValueError("The research answer must include at least one citation")

        source_count = len(state["sources"])
        invalid_numbers = sorted(
            {number for number in citation_numbers if not 1 <= number <= source_count}
        )
        if invalid_numbers:
            raise ValueError(
                f"The research answer cited unavailable sources: {invalid_numbers}"
            )

        return {}

    builder = StateGraph(
        WebResearchState,
        input_schema=WebResearchInput,
        output_schema=WebResearchOutput,
    )
    builder.add_node("validate_request", validate_request)
    builder.add_node("plan_search", plan_search)
    builder.add_node("search_web", search_web)
    builder.add_node("synthesize_answer", synthesize_answer)
    builder.add_node("validate_answer", validate_answer)
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "plan_search")
    builder.add_edge("plan_search", "search_web")
    builder.add_edge("search_web", "synthesize_answer")
    builder.add_edge("synthesize_answer", "validate_answer")
    builder.add_edge("validate_answer", END)
    return builder.compile()
