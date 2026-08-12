"""Bounded model-generated plans for one web search."""

from datetime import date

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, model_validator

from oris.prompts import load_system_prompt
from oris.search import (
    DomainName,
    NonEmptyText,
    SearchCategory,
    SearchTimeRange,
    WebSearchRequest,
)

SEARCH_PLANNING_SYSTEM_PROMPT = load_system_prompt("search_planning_system.txt")


class SearchPlan(BaseModel):
    """One provider-independent query and its optional search controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search_query: NonEmptyText = Field(
        max_length=400,
        description=(
            "A concise web-search query that preserves the question's exact names, "
            "versions, dates, and meaning."
        ),
    )
    include_domains: tuple[DomainName, ...] = Field(
        default=(),
        max_length=3,
        description=(
            "Hostnames explicitly requested in the research question, without a "
            "protocol or path. Empty when the question requests no domain restriction."
        ),
    )
    search_category: SearchCategory = Field(
        default="general",
        description=(
            "Use news only when the question explicitly requests news or news "
            "coverage; otherwise use general."
        ),
    )
    time_range: SearchTimeRange | None = Field(
        default=None,
        description=(
            "A recency filter only when the question clearly requests a recent "
            "time period."
        ),
    )
    start_date: date | None = Field(
        default=None,
        description="Inclusive calendar-date lower bound for an explicit date range.",
    )
    end_date: date | None = Field(
        default=None,
        description="Exclusive calendar-date upper bound for an explicit date range.",
    )

    @model_validator(mode="after")
    def validate_date_controls(self) -> "SearchPlan":
        """Require one valid relative or absolute date filter."""
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be provided together")
        if self.start_date is not None and self.start_date >= self.end_date:
            raise ValueError("start_date must be earlier than end_date")
        if self.time_range is not None and self.start_date is not None:
            raise ValueError("time_range cannot be combined with absolute dates")
        return self


def create_search_plan(
    model: BaseChatModel,
    question: str,
    *,
    current_date: date,
) -> SearchPlan:
    """Ask the model for one validated search plan without executing it."""
    validated_question = WebSearchRequest(query=question).query
    structured_model = model.with_structured_output(
        SearchPlan,
        method="json_schema",
    )
    plan = structured_model.invoke(
        [
            (
                "system",
                SEARCH_PLANNING_SYSTEM_PROMPT,
            ),
            (
                "human",
                f"Current date: {current_date.isoformat()}\n"
                f"Research question: {validated_question}",
            ),
        ],
        max_completion_tokens=256,
    )
    if not isinstance(plan, SearchPlan):
        raise TypeError("The search-planning model returned an invalid result type")
    return plan
