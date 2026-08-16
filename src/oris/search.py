"""Application-owned contracts for web search."""

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
