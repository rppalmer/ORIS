"""Application-owned contracts for web search."""

import re
from datetime import date
from typing import Annotated, Literal, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_serializer,
    model_validator,
)

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
DomainName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=253,
        pattern=(
            r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
        ),
    ),
]

SearchTimeRange = Literal["day", "week", "month", "year"]
SearchCategory = Literal["general", "news"]

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


class WebSearchRequest(BaseModel):
    """A provider-independent web-search request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: NonEmptyText
    include_domains: tuple[DomainName, ...] = Field(default=(), max_length=10)
    search_category: SearchCategory = "general"
    time_range: SearchTimeRange | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_date_controls(self) -> "WebSearchRequest":
        """Require one valid relative or absolute date filter."""
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be provided together")
        if self.start_date is not None and self.start_date >= self.end_date:
            raise ValueError("start_date must be earlier than end_date")
        if self.time_range is not None and self.start_date is not None:
            raise ValueError("time_range cannot be combined with absolute dates")
        return self

    @model_validator(mode="after")
    def drop_a_bounded_date_from_the_query(self) -> "WebSearchRequest":
        """Remove a calendar date from the query text when dates already bound it.

        A date in the query does not restrict publication dates. It matches
        pages that *mention* that date, and those are usually published the day
        after it, so it pulls results out of the very range `start_date` and
        `end_date` ask for. Measured against Tavily on 2026-08-31: the same
        query over the same one-day bound returned four articles from the
        requested day with no date in the text, and five from the day after with
        it. The `weekday-ai-news` scheduled run then failed outright, because an
        empty result set is a hard error.

        The planning prompt asks for this too, but a prompt is advice, and the
        bounds do not always come from the plan -- a caller can supply them,
        which is what every scheduled job does. This is the one place every
        request passes through, so the rule is enforced here.
        """
        if self.start_date is None:
            return self
        stripped = " ".join(_ISO_DATE.sub("", self.query).split())
        if stripped and stripped != self.query:
            # The model is frozen, and Pydantic requires an after-validator to
            # return `self` rather than a copy, so the field is set directly.
            object.__setattr__(self, "query", stripped)
        return self


class WebSearchResult(BaseModel):
    """One normalized source returned by a web-search provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: NonEmptyText
    url: HttpUrl
    snippet: NonEmptyText
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    published_at: AwareDatetime | None = None

    @field_serializer("url")
    def serialize_url(self, url: HttpUrl) -> str:
        """Serialize validated URLs as strings at external boundaries."""
        return str(url)


class WebSearchResponse(BaseModel):
    """Normalized search evidence plus minimal diagnostic metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: NonEmptyText
    results: tuple[WebSearchResult, ...] = Field(min_length=1)
    provider: NonEmptyText
    provider_request_id: NonEmptyText | None = None


class WebSearch(Protocol):
    """Provider-independent search capability used by application workflows.

    Asynchronous because a search crosses the network, and a synchronous
    implementation has nowhere to put a deadline: Python cannot cancel a
    blocking call in place, so neither the workflow nor the caller can bound
    it. Awaiting one leaves the request interruptible by whatever is above it.
    """

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        """Return normalized web evidence for one validated request."""
        ...


class SearchProviderError(RuntimeError):
    """A web-search provider failed or returned an unusable response."""
