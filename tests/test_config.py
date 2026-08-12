"""Tests for validated application configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oris.config import Settings

VALID_TEST_SETTINGS = {
    "LOCAL_LLM_BASE_URL": "http://llm.test/v1",
    "LOCAL_LLM_MODEL": "local-test-model",
    "LOCAL_LLM_API_KEY": "local-test-key",
    "LOCAL_LLM_TIMEOUT_SECONDS": "45",
    "TAVILY_API_KEY": "tavily-test-key",
    "LANGSMITH_TRACING": "false",
}


def test_settings_accept_valid_explicit_values() -> None:
    """Tests can construct settings without reading the developer's .env file."""
    settings = Settings(_env_file=None, **VALID_TEST_SETTINGS)

    assert str(settings.local_llm_base_url) == "http://llm.test/v1"
    assert settings.local_llm_model == "local-test-model"
    assert settings.local_llm_timeout_seconds == 45
    assert settings.langsmith_tracing is False
    assert settings.local_tracing_enabled is False
    assert str(settings.phoenix_collector_endpoint) == (
        "http://127.0.0.1:6006/v1/traces"
    )
    assert settings.checkpoint_database_path == Path("data/checkpoints.sqlite")
    assert settings.knowledge_database_path == Path("data/knowledge.sqlite")
    assert settings.net_razor_python_executable is None


def test_settings_accept_net_razor_python_executable() -> None:
    """The local Net-Razor checkout remains machine configuration."""
    values = {
        **VALID_TEST_SETTINGS,
        "NET_RAZOR_PYTHON_EXECUTABLE": "/path/to/net-razor/.venv/bin/python",
    }

    settings = Settings(_env_file=None, **values)

    assert settings.net_razor_python_executable == Path(
        "/path/to/net-razor/.venv/bin/python"
    )


def test_secrets_are_masked_in_settings_representation() -> None:
    """Secret values do not appear in routine settings diagnostics."""
    settings = Settings(_env_file=None, **VALID_TEST_SETTINGS)

    settings_representation = repr(settings)
    assert "local-test-key" not in settings_representation
    assert "tavily-test-key" not in settings_representation


def test_missing_settings_name_each_required_environment_variable() -> None:
    """Startup errors identify all missing required configuration."""
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    error_text = str(error.value)
    assert "LOCAL_LLM_BASE_URL" in error_text
    assert "LOCAL_LLM_MODEL" in error_text
    assert "LOCAL_LLM_API_KEY" in error_text
    assert "TAVILY_API_KEY" in error_text


def test_langsmith_tracing_cannot_be_enabled() -> None:
    """The local-first application rejects accidental LangSmith tracing."""
    values = {**VALID_TEST_SETTINGS, "LANGSMITH_TRACING": "true"}

    with pytest.raises(ValidationError, match="LANGSMITH_TRACING"):
        Settings(_env_file=None, **values)


def test_local_llm_timeout_must_be_positive() -> None:
    """A non-positive timeout cannot disable the request boundary."""
    values = {**VALID_TEST_SETTINGS, "LOCAL_LLM_TIMEOUT_SECONDS": "0"}

    with pytest.raises(ValidationError, match="LOCAL_LLM_TIMEOUT_SECONDS"):
        Settings(_env_file=None, **values)


def test_validation_errors_hide_raw_input_values() -> None:
    """Configuration failures do not echo credentials from the input."""
    values = {**VALID_TEST_SETTINGS, "LANGSMITH_TRACING": "true"}

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **values)

    error_text = str(error.value)
    assert "local-test-key" not in error_text
    assert "tavily-test-key" not in error_text
    assert "input_value" not in error_text
