"""Validated application configuration loaded from the environment."""

from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
NonEmptySecret = Annotated[SecretStr, Field(min_length=1)]
ConfiguredPath = Annotated[Path, AfterValidator(Path.expanduser)]
"""A path from the environment, with a leading `~` resolved to the home directory.

`Path` keeps `~` as an ordinary character, so an override written the way people
actually write it would silently create a directory named `~` beside whatever
the process was started in — the same invisible split this file exists to close.
"""

ORIS_HOME = Path.home() / ".oris"
"""Where ORIS keeps the data and credentials it owns, and the one fixed anchor.

A relative default resolves against whatever directory the process was started
in, so the interactive session, the scheduler under its LaunchAgent, and a
shell one level down each quietly built their own conversation history and
knowledge index. Nothing reported the split; `/recall` simply stopped finding
yesterday's answers. Every path below is absolute for that reason, and the
matching environment variable still overrides it, which is how an existing
installation keeps pointing at the directories it already has.
"""

DEFAULT_MAX_HISTORY_TOKENS = 8000
"""Conversation tokens sent to the model per turn, excluding the system prompt.

Sized for the smallest context window ORIS is expected to run against, leaving
room for the system prompt and the reserved completion budget.
"""


class Settings(BaseSettings):
    """Configuration required by ORIS's first external adapters."""

    model_config = SettingsConfigDict(
        env_file=ORIS_HOME / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    local_llm_base_url: HttpUrl = Field(validation_alias="LOCAL_LLM_BASE_URL")
    local_llm_model: NonEmptyString = Field(validation_alias="LOCAL_LLM_MODEL")
    local_llm_api_key: NonEmptySecret = Field(validation_alias="LOCAL_LLM_API_KEY")
    local_llm_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        validation_alias="LOCAL_LLM_TIMEOUT_SECONDS",
    )
    local_llm_max_history_tokens: int = Field(
        default=DEFAULT_MAX_HISTORY_TOKENS,
        gt=0,
        validation_alias="LOCAL_LLM_MAX_HISTORY_TOKENS",
    )
    tavily_api_key: NonEmptySecret = Field(validation_alias="TAVILY_API_KEY")
    langsmith_tracing: bool = Field(
        default=False,
        validation_alias="LANGSMITH_TRACING",
    )
    local_tracing_enabled: bool = Field(
        default=False,
        validation_alias="LOCAL_TRACING_ENABLED",
    )
    phoenix_collector_endpoint: HttpUrl = Field(
        default="http://127.0.0.1:6006/v1/traces",
        validation_alias="PHOENIX_COLLECTOR_ENDPOINT",
    )
    # Phoenix's own variable, so an operator who already sets it keeps working.
    # This is the single definition: `oris-phoenix` derives the collector's
    # environment from this setting rather than restating the default, so the
    # directory ORIS reads and the one the collector writes cannot drift.
    phoenix_working_directory: ConfiguredPath = Field(
        default=ORIS_HOME / "traces" / "phoenix",
        validation_alias="PHOENIX_WORKING_DIR",
    )
    checkpoint_database_path: ConfiguredPath = Field(
        default=ORIS_HOME / "data" / "checkpoints.sqlite",
        validation_alias="ORIS_CHECKPOINT_DB_PATH",
    )
    knowledge_database_path: ConfiguredPath = Field(
        default=ORIS_HOME / "data" / "knowledge.sqlite",
        validation_alias="ORIS_KNOWLEDGE_DB_PATH",
    )
    net_razor_python_executable: ConfiguredPath | None = Field(
        default=None,
        validation_alias="NET_RAZOR_PYTHON_EXECUTABLE",
    )
    threatsyft_python_executable: ConfiguredPath | None = Field(
        default=None,
        validation_alias="THREATSYFT_PYTHON_EXECUTABLE",
    )
    threatsyft_root: ConfiguredPath | None = Field(
        default=None,
        validation_alias="THREATSYFT_ROOT",
    )
    threat_report_directory: ConfiguredPath = Field(
        default=ORIS_HOME / "artifacts" / "threat",
        validation_alias="ORIS_THREAT_REPORT_DIR",
    )
    threat_report_retention_days: int = Field(
        default=30,
        ge=1,
        validation_alias="ORIS_THREAT_REPORT_RETENTION_DAYS",
    )

    @property
    def trace_database_path(self) -> Path:
        """Phoenix's SQLite file, whether or not Phoenix is currently running."""
        return self.phoenix_working_directory / "phoenix.db"

    @property
    def export_directory(self) -> Path:
        """Where the terminal interface writes exported activity.

        Anchored to the fixed root rather than derived from the threat-report
        directory. Deriving it meant that pointing `ORIS_THREAT_REPORT_DIR`
        somewhere else silently moved the exports as well — a setting doing a
        second, undocumented thing.
        """
        return ORIS_HOME / "artifacts" / "exports"

    @property
    def service_log_path(self) -> Path:
        """Where a stdio MCP server's own logging goes while the interface runs.

        Anchored to the fixed root for the same reason as the exports above: it
        should not move because some unrelated directory setting was pointed
        elsewhere.
        """
        return ORIS_HOME / "logs" / "mcp-servers.log"

    @property
    def phoenix_url(self) -> str:
        """The Phoenix UI, derived from the endpoint traces are already sent to."""
        endpoint = str(self.phoenix_collector_endpoint)
        return endpoint.removesuffix("/v1/traces").rstrip("/")

    @field_validator("langsmith_tracing")
    @classmethod
    def require_langsmith_tracing_disabled(cls, value: bool) -> bool:
        """Reject configuration that would send traces to LangSmith."""
        if value:
            raise ValueError("LANGSMITH_TRACING must remain disabled")
        return value


def load_settings() -> Settings:
    """Load and validate settings explicitly at application startup."""
    return Settings()
