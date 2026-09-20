"""Bounded model-chosen selection of which search results to read."""

from typing import Annotated

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from oris.prompts import load_system_prompt
from oris.search import WebSearchResult

SOURCE_SELECTION_SYSTEM_PROMPT = load_system_prompt("source_selection_system.txt")

CandidateNumber = Annotated[int, Field(ge=1)]


class SourceSelection(BaseModel):
    """The candidates worth reading, as positions in the list supplied.

    Positions rather than URLs on purpose. The calling code already holds the
    candidate list, so asking a model to retype a URL adds a way to fail and
    buys nothing: Community Research measured Qwen3.5 keeping a handle and
    dropping digits out of the middle of a 19-digit id, which invalidated the
    whole answer about one run in three.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chosen: tuple[CandidateNumber, ...] = Field(
        min_length=1,
        description=(
            "The numbers of the candidates worth reading in full, most useful "
            "first. Choose as few as will answer the question."
        ),
    )


def _describe(candidates: tuple[WebSearchResult, ...]) -> str:
    """Show each candidate as the reader of a results page would see it."""
    return "\n\n".join(
        f"{number}. {candidate.title}\n"
        f"   {candidate.url}\n"
        f"   published: {candidate.published_at or 'unknown'}\n"
        f"   {candidate.snippet or '(no preview)'}"
        for number, candidate in enumerate(candidates, start=1)
    )


def select_sources(
    model: BaseChatModel,
    question: str,
    candidates: tuple[WebSearchResult, ...],
    *,
    limit: int,
) -> tuple[WebSearchResult, ...]:
    """Return the candidates worth reading, never more than `limit` of them.

    Falls back to the engine's own order when the model returns nothing usable.
    That was the whole behaviour before this step existed, so the worst case
    here is what the previous version always did.
    """
    if len(candidates) <= limit:
        return candidates
    structured_model = model.with_structured_output(
        SourceSelection,
        method="json_schema",
    )
    selection = structured_model.invoke(
        [
            ("system", SOURCE_SELECTION_SYSTEM_PROMPT),
            (
                "human",
                f"Research question: {question}\n\n"
                f"Read at most {limit} of these.\n\n"
                f"Candidates:\n{_describe(candidates)}",
            ),
        ],
        max_completion_tokens=256,
    )
    if not isinstance(selection, SourceSelection):
        raise TypeError("The source-selection model returned an invalid result type")
    # A number outside the list is dropped rather than raised on. The model is
    # choosing, not computing, and a bad number costs one candidate; failing
    # the run would cost the whole answer.
    picked = list(
        dict.fromkeys(
            number for number in selection.chosen if 1 <= number <= len(candidates)
        )
    )[:limit]
    if not picked:
        return candidates[:limit]
    return tuple(candidates[number - 1] for number in picked)
