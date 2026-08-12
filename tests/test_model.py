"""Tests for construction of the official local model integration."""

from json import JSONDecodeError
from unittest.mock import patch

import httpx
import pytest
from openai import APITimeoutError

from oris.config import Settings
from oris.model import create_chat_model

TEST_SETTINGS = {
    "LOCAL_LLM_BASE_URL": "http://llm.test/v1",
    "LOCAL_LLM_MODEL": "local-test-model",
    "LOCAL_LLM_API_KEY": "local-test-key",
    "LOCAL_LLM_TIMEOUT_SECONDS": 45,
    "TAVILY_API_KEY": "tavily-test-key",
    "LANGSMITH_TRACING": False,
}


def test_create_chat_model_uses_validated_settings() -> None:
    """The factory configures ChatOpenAI without contacting the model server."""
    settings = Settings(_env_file=None, **TEST_SETTINGS)

    model = create_chat_model(settings)

    assert model.model_name == "local-test-model"
    assert model.openai_api_base == "http://llm.test/v1"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "local-test-key"


def test_create_chat_model_has_predictable_initial_generation_settings() -> None:
    """Sampling and automatic transport retries start from explicit values."""
    settings = Settings(_env_file=None, **TEST_SETTINGS)

    model = create_chat_model(settings)

    assert model.temperature == 0
    assert model.max_retries == 0
    assert model.request_timeout == 45


def test_chat_model_propagates_timeout_without_retrying() -> None:
    """The configured timeout reaches HTTPX and fails without a retry."""
    settings = Settings(
        _env_file=None,
        **{
            **TEST_SETTINGS,
            "LOCAL_LLM_TIMEOUT_SECONDS": 0.1,
        },
    )
    model = create_chat_model(settings)
    observed_timeouts: list[dict[str, float]] = []

    def raise_read_timeout(
        _client: httpx.Client,
        request: httpx.Request,
        **_kwargs: object,
    ) -> httpx.Response:
        observed_timeouts.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("Controlled test timeout", request=request)

    with (
        patch.object(
            httpx.Client,
            "send",
            autospec=True,
            side_effect=raise_read_timeout,
        ),
        pytest.raises(APITimeoutError),
    ):
        model.invoke("Respond after the configured timeout.")

    assert len(observed_timeouts) == 1
    assert set(observed_timeouts[0].values()) == {0.1}


def test_chat_model_exposes_malformed_json_without_retrying() -> None:
    """Invalid response JSON is exposed after one request attempt."""
    settings = Settings(_env_file=None, **TEST_SETTINGS)
    model = create_chat_model(settings)
    request_count = 0

    def return_malformed_json(
        _client: httpx.Client,
        request: httpx.Request,
        **_kwargs: object,
    ) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            status_code=200,
            content=b"not valid json",
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with (
        patch.object(
            httpx.Client,
            "send",
            autospec=True,
            side_effect=return_malformed_json,
        ),
        pytest.raises(JSONDecodeError, match="Expecting value"),
    ):
        model.invoke("Return a response that the client can parse.")

    assert request_count == 1
