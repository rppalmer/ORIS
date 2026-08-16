"""Deterministic retrieval from ORIS's local knowledge archive."""

import json
import re
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
from oris.prompts import load_system_prompt, with_current_date

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


MAX_DOCUMENT_CHARACTERS = 3000
"""How much of one archived document is worth sending to answer a question.

Archived documents are whole prior exchanges, and a `/threat report` turn
archives its entire fenced JSON pivot — a single real one-indicator report is
over 5,000 characters. Five of those retrieved together would fill the prompt
before the question and the system instructions were added, and nothing in the
retrieval path bounds their size. The opening of a document is the part that
says what it was about, so a cut tail costs the least.
"""

TRUNCATION_NOTICE = "\n…[truncated]"


def _format_evidence(documents: tuple[KnowledgeDocument, ...]) -> str:
    """Serialize retrieved documents with stable, one-based source numbers."""
    evidence = [
        {
            "source_number": source_number,
            "title": document.title,
            "source_type": document.source_type,
            "source_ref": document.source_ref,
            "created_at": document.created_at.isoformat(),
            "content": _bounded(document.content),
        }
        for source_number, document in enumerate(documents, start=1)
    ]
    return json.dumps(evidence, ensure_ascii=False, indent=2)


def _bounded(content: str) -> str:
    """Cut an oversized document, saying so rather than trailing off silently."""
    if len(content) <= MAX_DOCUMENT_CHARACTERS:
        return content
    return content[:MAX_DOCUMENT_CHARACTERS] + TRUNCATION_NOTICE


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
                    with_current_date(LOCAL_KNOWLEDGE_SYSTEM_PROMPT),
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

    def validate_answer(state: LocalKnowledgeState) -> dict[str, object]:
        """Reject an answer that points at evidence which was never supplied.

        Unlike Web Research, this does not demand at least one citation. The
        two prompts differ deliberately: this specialist is told to say when
        the archive does not answer the question, and that answer is honest
        precisely because it cites nothing. What is never acceptable is a
        number the reader cannot follow back to a retrieved document.
        """
        if not state["sources"]:
            return {}

        cited = {int(number) for number in re.findall(r"\[(\d+)\]", state["answer"])}
        unavailable = sorted(
            number for number in cited if not 1 <= number <= len(state["sources"])
        )
        if unavailable:
            raise ValueError(
                f"The archive answer cited unavailable sources: {unavailable}"
            )
        return {}

    builder = StateGraph(
        LocalKnowledgeState,
        input_schema=LocalKnowledgeInput,
        output_schema=LocalKnowledgeOutput,
    )
    builder.add_node("plan_search", plan_search)
    builder.add_node("retrieve_knowledge", retrieve_knowledge)
    builder.add_node("answer_from_knowledge", answer_from_knowledge)
    builder.add_node("validate_answer", validate_answer)
    builder.add_edge(START, "plan_search")
    builder.add_edge("plan_search", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "answer_from_knowledge")
    builder.add_edge("answer_from_knowledge", "validate_answer")
    builder.add_edge("validate_answer", END)
    return builder.compile()
