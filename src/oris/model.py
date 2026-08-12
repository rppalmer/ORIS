"""Construction of the application's official chat-model integration."""

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
    )
