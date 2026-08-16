"""Tests for validated application configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oris.config import ORIS_HOME, Settings

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
    assert settings.checkpoint_database_path == ORIS_HOME / "data/checkpoints.sqlite"
    assert settings.knowledge_database_path == ORIS_HOME / "data/knowledge.sqlite"
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


def test_the_trace_database_follows_phoenix_own_variable() -> None:
    """Reading PHOENIX_WORKING_DIR is what keeps the two from drifting apart."""
    settings = Settings(_env_file=None, **VALID_TEST_SETTINGS)
    moved = Settings(
        _env_file=None,
        **VALID_TEST_SETTINGS,
        PHOENIX_WORKING_DIR="/var/phoenix",
    )

    assert settings.trace_database_path == ORIS_HOME / "traces/phoenix/phoenix.db"
    assert moved.trace_database_path == Path("/var/phoenix/phoenix.db")


def test_storage_paths_do_not_depend_on_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two ORIS processes must reach one store however they were started.

    The interactive session runs from wherever the user happens to be and the
    scheduler runs from its LaunchAgent's directory. While the defaults were
    relative each of them built a private conversation history and knowledge
    index, and nothing reported it: `/recall` just stopped finding answers.
    """
    from_the_project = Settings(_env_file=None, **VALID_TEST_SETTINGS)
    monkeypatch.chdir(tmp_path)
    from_elsewhere = Settings(_env_file=None, **VALID_TEST_SETTINGS)

    for name in (
        "checkpoint_database_path",
        "knowledge_database_path",
        "threat_report_directory",
        "phoenix_working_directory",
    ):
        assert getattr(from_the_project, name).is_absolute()
        assert getattr(from_the_project, name) == getattr(from_elsewhere, name)


def test_a_configured_path_expands_a_leading_home_shortcut() -> None:
    """`~/…` is how these overrides get written, and Path keeps `~` literal.

    Unexpanded it names a directory called `~` beside the working directory,
    which is the same invisible split the absolute defaults exist to close.
    """
    settings = Settings(
        _env_file=None,
        **VALID_TEST_SETTINGS,
        ORIS_KNOWLEDGE_DB_PATH="~/elsewhere/knowledge.sqlite",
    )

    assert (
        settings.knowledge_database_path == Path.home() / "elsewhere/knowledge.sqlite"
    )


def test_the_phoenix_url_is_derived_from_the_collector_endpoint() -> None:
    """One address is configured; the UI is the same server without the path."""
    settings = Settings(
        _env_file=None,
        **VALID_TEST_SETTINGS,
        PHOENIX_COLLECTOR_ENDPOINT="http://127.0.0.1:7000/v1/traces",
    )

    assert settings.phoenix_url == "http://127.0.0.1:7000"
