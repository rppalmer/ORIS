"""Opt-in contract tests for the configured local chat model."""

import os
from typing import Literal

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from oris.config import Settings
from oris.model import create_chat_model

LIVE_TESTS_ENABLED = os.environ.get("ORIS_RUN_LIVE_TESTS") == "1"


class ReadinessResponse(BaseModel):
    """Small schema used to verify structured-output contracts."""

    status: Literal["ready"] = Field(description="The fixed readiness status")
    explanation: str = Field(description="A short readiness explanation")


def get_project_status(project_name: str) -> str:
    """Return the current status code for a project."""
    return f"{project_name} status code: MODEL-CONTRACT-PASSED"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Set ORIS_RUN_LIVE_TESTS=1 to contact the configured model.",
)
def test_chat_model_returns_a_non_empty_response() -> None:
    """oMLX satisfies the basic ChatOpenAI invocation contract."""
    settings = Settings(TAVILY_API_KEY="unused-by-model-contract")
    model = create_chat_model(settings)

    response = model.invoke("Reply with a short confirmation that you are ready.")

    assert isinstance(response.content, str)
    assert response.content.strip()


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Set ORIS_RUN_LIVE_TESTS=1 to contact the configured model.",
)
def test_chat_model_streams_incremental_text() -> None:
    """oMLX yields multiple partial text chunks through ChatOpenAI."""
    settings = Settings(TAVILY_API_KEY="unused-by-model-contract")
    model = create_chat_model(settings)

    chunks = list(
        model.stream(
            "Write one sentence of at least twenty words about predictable software."
        )
    )
    text_chunks = [chunk.text for chunk in chunks if chunk.text]

    assert len(text_chunks) > 1
    assert "".join(text_chunks).strip()


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Set ORIS_RUN_LIVE_TESTS=1 to contact the configured model.",
)
def test_chat_model_returns_validated_structured_output() -> None:
    """oMLX satisfies ChatOpenAI's native JSON-schema output contract."""
    settings = Settings(TAVILY_API_KEY="unused-by-model-contract")
    model = create_chat_model(settings)
    structured_model = model.with_structured_output(
        ReadinessResponse,
        method="json_schema",
    )

    response = structured_model.invoke(
        "Confirm that you are ready and provide one short explanation."
    )

    assert isinstance(response, ReadinessResponse)
    assert response.status == "ready"
    assert response.explanation.strip()


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Set ORIS_RUN_LIVE_TESTS=1 to contact the configured model.",
)
def test_chat_model_returns_validated_function_call_output() -> None:
    """oMLX satisfies ChatOpenAI's function-calling output contract."""
    settings = Settings(TAVILY_API_KEY="unused-by-model-contract")
    model = create_chat_model(settings)
    structured_model = model.with_structured_output(
        ReadinessResponse,
        method="function_calling",
    )

    response = structured_model.invoke(
        "Confirm that you are ready and provide one short explanation."
    )

    assert isinstance(response, ReadinessResponse)
    assert response.status == "ready"
    assert response.explanation.strip()


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Set ORIS_RUN_LIVE_TESTS=1 to contact the configured model.",
)
def test_chat_model_completes_a_tool_round_trip() -> None:
    """oMLX accepts a tool result and uses it in the final response."""
    settings = Settings(TAVILY_API_KEY="unused-by-model-contract")
    model = create_chat_model(settings)
    model_with_tools = model.bind_tools([get_project_status])
    user_message = HumanMessage(
        "Use get_project_status to check ORIS. "
        "Then report the exact status code returned by the tool."
    )

    tool_request = model_with_tools.invoke([user_message])

    assert len(tool_request.tool_calls) == 1
    tool_call = tool_request.tool_calls[0]
    assert tool_call["name"] == "get_project_status"
    assert tool_call["args"] == {"project_name": "ORIS"}

    tool_result = get_project_status(**tool_call["args"])
    tool_message = ToolMessage(
        content=tool_result,
        tool_call_id=tool_call["id"],
        name=tool_call["name"],
    )
    final_response = model_with_tools.invoke([user_message, tool_request, tool_message])

    assert not final_response.tool_calls
    assert "MODEL-CONTRACT-PASSED" in final_response.text
