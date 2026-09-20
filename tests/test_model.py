"""Tests for construction of the official local model integration."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from json import JSONDecodeError
from threading import Thread
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


@contextmanager
def _stub_completions_server() -> Iterator[str]:
    """Serve chat completions over a real keep-alive socket.

    A patched transport cannot show this behaviour. The connection pool sits
    below the transport, so only a genuine socket is left pooled for a later
    event loop to trip over.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {
                    "id": "stub",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "local-test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            """Keep the stub silent so test output stays pristine."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_chat_model_answers_a_job_running_in_a_later_event_loop() -> None:
    """One model serves every scheduled job, and each job runs its own loop.

    The scheduler builds the model once and keeps it for the life of the
    process, while every firing runs in a fresh event loop that is closed when
    the job ends. Nothing the first job leaves behind may reach back into its
    dead loop, or the next job dies before it opens a connection.
    """
    with _stub_completions_server() as base_url:
        settings = Settings(
            _env_file=None,
            **{**TEST_SETTINGS, "LOCAL_LLM_BASE_URL": base_url},
        )
        model = create_chat_model(settings)

        first = asyncio.run(model.ainvoke("The job that runs first."))
        second = asyncio.run(model.ainvoke("The job that runs an hour later."))

    assert first.content == "ok"
    assert second.content == "ok"
