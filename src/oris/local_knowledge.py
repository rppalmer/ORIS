"""Deterministic retrieval from ORIS's local knowledge archive."""

import json
from typing import NotRequired, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.knowledge import (
    KnowledgeDocument,
    KnowledgeRepository,
    KnowledgeSortOrder,
    KnowledgeSource,
    NonEmptyString,
    knowledge_search_terms,
)
from oris.prompts import load_system_prompt

NO_KNOWLEDGE_MESSAGE = "I couldn't find relevant information in the local archive."
LOCAL_KNOWLEDGE_SYSTEM_PROMPT = load_system_prompt("local_knowledge_system.txt")
LOCAL_KNOWLEDGE_PLANNING_SYSTEM_PROMPT = load_system_prompt(
    "local_knowledge_planning_system.txt"
)


class LocalKnowledgePlan(BaseModel):
    """One bounded search plan for the local archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search_query: NonEmptyString = Field(
        max_length=400,
        description="Concise terms that identify the requested archived information.",
    )
    source_type: KnowledgeSource | None = Field(
        description=(
            "chat for earlier conversations, scheduled_run for scheduled reports, "
            "or null when either may answer the question."
        )
    )
    sort_order: KnowledgeSortOrder = Field(
        description="newest when recency matters; otherwise relevance."
    )


class LocalKnowledgeInput(TypedDict):
    """Public input accepted by the Local Knowledge graph."""

    query: str


class LocalKnowledgeOutput(TypedDict):
    """Public output returned by the Local Knowledge graph."""

    answer: str
    sources: tuple[KnowledgeDocument, ...]


class LocalKnowledgeState(TypedDict):
    """Internal state shared by Local Knowledge nodes."""

    query: str
    search_query: NotRequired[str]
    source_type: NotRequired[KnowledgeSource | None]
    sort_order: NotRequired[KnowledgeSortOrder]
    answer: NotRequired[str]
    sources: NotRequired[tuple[KnowledgeDocument, ...]]


def _format_evidence(documents: tuple[KnowledgeDocument, ...]) -> str:
    """Serialize retrieved documents with stable, one-based source numbers."""
    evidence = [
        {
            "source_number": source_number,
            "title": document.title,
            "source_type": document.source_type,
            "source_ref": document.source_ref,
            "created_at": document.created_at.isoformat(),
            "content": document.content,
        }
        for source_number, document in enumerate(documents, start=1)
    ]
    return json.dumps(evidence, ensure_ascii=False, indent=2)


def create_local_knowledge_graph(
    repository: KnowledgeRepository,
    model: BaseChatModel,
) -> CompiledStateGraph:
    """Compile the Local Knowledge graph with its injected dependencies."""
    planning_model = model.with_structured_output(
        LocalKnowledgePlan,
        method="json_schema",
    )

    def plan_search(state: LocalKnowledgeState) -> dict[str, object]:
        knowledge_search_terms(state["query"])
        plan = planning_model.invoke(
            [
                ("system", LOCAL_KNOWLEDGE_PLANNING_SYSTEM_PROMPT),
                ("human", f"Archive question:\n{state['query']}"),
            ],
            max_completion_tokens=256,
        )
        if not isinstance(plan, LocalKnowledgePlan):
            raise TypeError("The Local Knowledge planner returned an invalid result")
        return plan.model_dump()

    def retrieve_knowledge(
        state: LocalKnowledgeState,
    ) -> dict[str, tuple[KnowledgeDocument, ...]]:
        result_limit = 1 if state["sort_order"] == "newest" else 5
        return {
            "sources": repository.search(
                state["search_query"],
                source_type=state["source_type"],
                sort_order=state["sort_order"],
                limit=result_limit,
            )
        }

    def answer_from_knowledge(state: LocalKnowledgeState) -> dict[str, str]:
        sources = state["sources"]
        if not sources:
            return {"answer": NO_KNOWLEDGE_MESSAGE}

        response = model.invoke(
            [
                (
                    "system",
                    LOCAL_KNOWLEDGE_SYSTEM_PROMPT,
                ),
                (
                    "human",
                    f"Question:\n{state['query']}\n\n"
                    f"Archive result order: {state['sort_order']}\n\n"
                    f"Archive evidence:\n{_format_evidence(sources)}",
                ),
            ],
            max_completion_tokens=512,
        )
        answer = response.text.strip()
        if not answer:
            raise ValueError("The local knowledge model returned an empty answer")
        return {"answer": answer}

    builder = StateGraph(
        LocalKnowledgeState,
        input_schema=LocalKnowledgeInput,
        output_schema=LocalKnowledgeOutput,
    )
    builder.add_node("plan_search", plan_search)
    builder.add_node("retrieve_knowledge", retrieve_knowledge)
    builder.add_node("answer_from_knowledge", answer_from_knowledge)
    builder.add_edge(START, "plan_search")
    builder.add_edge("plan_search", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "answer_from_knowledge")
    builder.add_edge("answer_from_knowledge", END)
    return builder.compile()
