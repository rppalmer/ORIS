"""Tavily implementation of the application's web-search boundary."""

from email.utils import parsedate_to_datetime
from typing import Any

from langchain_core.tools import ToolException
from langchain_tavily import TavilySearch
from pydantic import ValidationError

from oris.config import Settings
from oris.search import (
    SearchCategory,
    SearchProviderError,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)


def create_tavily_search(
    settings: Settings,
    *,
    topic: SearchCategory = "general",
) -> TavilySearch:
    """Create Tavily search with fixed, conservative request parameters.

    `handle_tool_error=False` keeps the tool's own `ToolException` — notably the
    explanation it raises for an empty result set — instead of having it
    flattened into a plain string that this adapter cannot tell from a
    malformed response.
    """
    return TavilySearch(
        tavily_api_key=settings.tavily_api_key.get_secret_value(),
        handle_tool_error=False,
        max_results=5,
        topic=topic,
        search_depth="basic",
        auto_parameters=False,
        include_answer=False,
        include_raw_content=False,
        include_images=False,
        include_image_descriptions=False,
        include_favicon=False,
        include_usage=False,
    )


class TavilyWebSearch:
    """Invoke the official Tavily tool and normalize its response."""

    def __init__(
        self,
        general_search: TavilySearch,
        news_search: TavilySearch,
    ) -> None:
        self._general_search = general_search
        self._news_search = news_search

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        """Search Tavily once and return application-owned evidence."""
        arguments: dict[str, object] = {"query": request.query}
        if request.include_domains:
            arguments["include_domains"] = list(request.include_domains)
        if request.time_range is not None:
            arguments["time_range"] = request.time_range
        if request.start_date is not None:
            arguments["start_date"] = request.start_date.isoformat()
            arguments["end_date"] = request.end_date.isoformat()

        tool = (
            self._news_search
            if request.search_category == "news"
            else self._general_search
        )
        try:
            raw_response = tool.invoke(arguments)
        except ToolException as error:
            raise SearchProviderError(str(error)) from error
        if not isinstance(raw_response, dict):
            raise SearchProviderError("Tavily returned a non-object response")

        provider_error = raw_response.get("error")
        if provider_error is not None:
            if isinstance(provider_error, BaseException):
                raise SearchProviderError("Tavily search failed") from provider_error
            raise SearchProviderError(f"Tavily search failed: {provider_error}")

        raw_results = raw_response.get("results")
        if not isinstance(raw_results, list):
            raise SearchProviderError("Tavily response did not contain a results list")

        try:
            results = tuple(self._normalize_result(result) for result in raw_results)
            if request.start_date is not None:
                results = tuple(
                    result
                    for result in results
                    if result.published_at is not None
                    and request.start_date
                    <= result.published_at.date()
                    < request.end_date
                )
                if not results:
                    raise SearchProviderError(
                        "Tavily returned no dated results in the requested range"
                    )
            return WebSearchResponse(
                query=request.query,
                results=results,
                provider="tavily",
                provider_request_id=raw_response.get("request_id"),
                provider_response_time_seconds=raw_response.get("response_time"),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            request_id = raw_response.get("request_id")
            context = f" (request_id={request_id})" if request_id else ""
            raise SearchProviderError(
                f"Tavily returned an invalid response{context}"
            ) from error

    @staticmethod
    def _normalize_result(raw_result: Any) -> WebSearchResult:
        if not isinstance(raw_result, dict):
            raise TypeError("Tavily result must be an object")

        published_date = raw_result.get("published_date")
        if published_date is not None and not isinstance(published_date, str):
            raise TypeError("Tavily published_date must be a string")

        return WebSearchResult(
            title=raw_result["title"],
            url=raw_result["url"],
            snippet=raw_result["content"],
            relevance_score=raw_result.get("score"),
            published_at=(
                parsedate_to_datetime(published_date)
                if published_date is not None
                else None
            ),
        )
