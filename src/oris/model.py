"""Construction of the application's official chat-model integration."""

import httpx
from langchain_openai import ChatOpenAI

from oris.config import Settings


def create_chat_model(settings: Settings) -> ChatOpenAI:
    """Create the LangChain chat model configured for the local oMLX server."""
    return ChatOpenAI(
        model=settings.local_llm_model,
        base_url=str(settings.local_llm_base_url),
        api_key=settings.local_llm_api_key.get_secret_value(),
        temperature=0,
        max_retries=0,
        timeout=settings.local_llm_timeout_seconds,
        # No connection outlives the request that opened it.
        #
        # The scheduler builds this model once and shares it with every job,
        # but each job runs in its own event loop that is closed when the job
        # ends. A pooled connection belongs to the loop that opened it, so one
        # left over from an earlier job is a trap: the next job's first call
        # tries to retire it, reaches into the closed loop, and dies with
        # "Event loop is closed" -- which the OpenAI client reports as a
        # connection error, before it has opened any connection at all.
        #
        # That cost the overnight podcast job every other night through
        # September 2026. Failing cleared the stale connection, so the next run
        # worked and left a fresh one, which is why it alternated and read like
        # a flaky network.
        #
        # Keeping no idle connections removes the trap. The model server is on
        # this machine, so a new connection per request costs far less than one
        # token of the replies it carries.
        http_async_client=httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=0),
        ),
    )
