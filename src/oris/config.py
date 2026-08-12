"""Validated application configuration loaded from the environment."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, HttpUrl, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
NonEmptySecret = Annotated[SecretStr, Field(min_length=1)]

DEFAULT_MAX_HISTORY_TOKENS = 8000
"""Conversation tokens sent to the model per turn, excluding the system prompt.

Sized for the smallest context window ORIS is expected to run against, leaving
room for the system prompt and the reserved completion budget.
"""


class Settings(BaseSettings):
    """Configuration required by ORIS's first external adapters."""

    model_config = SettingsConfigDict(
        env_file=".env",
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
    checkpoint_database_path: Path = Field(
        default=Path("data/checkpoints.sqlite"),
        validation_alias="ORIS_CHECKPOINT_DB_PATH",
    )
    knowledge_database_path: Path = Field(
        default=Path("data/knowledge.sqlite"),
        validation_alias="ORIS_KNOWLEDGE_DB_PATH",
    )
    net_razor_python_executable: Path | None = Field(
        default=None,
        validation_alias="NET_RAZOR_PYTHON_EXECUTABLE",
    )
    threatsyft_python_executable: Path | None = Field(
        default=None,
        validation_alias="THREATSYFT_PYTHON_EXECUTABLE",
    )
    threatsyft_root: Path | None = Field(
        default=None,
        validation_alias="THREATSYFT_ROOT",
    )
    threat_report_directory: Path = Field(
        default=Path("artifacts/threat"),
        validation_alias="ORIS_THREAT_REPORT_DIR",
    )
    threat_report_retention_days: int = Field(
        default=30,
        ge=1,
        validation_alias="ORIS_THREAT_REPORT_RETENTION_DAYS",
    )

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
