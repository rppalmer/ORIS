"""Tests for running the local Phoenix trace collector."""

import os
from pathlib import Path

import pytest

from oris.config import Settings
from oris.phoenix import (
    PHOENIX_PACKAGE,
    install_command,
    phoenix_command,
    phoenix_environment,
    phoenix_executable,
)

TEST_SETTINGS = {
    "LOCAL_LLM_BASE_URL": "http://llm.test/v1",
    "LOCAL_LLM_MODEL": "local-test-model",
    "LOCAL_LLM_API_KEY": "local-test-key",
    "TAVILY_API_KEY": "tavily-test-key",
}


def test_the_collector_is_found_without_help_from_path(
    tmp_path: Path, monkeypatch
) -> None:
    """A LaunchAgent gets a minimal PATH, and this has to work there too.

    `uv tool install` links into `~/.local/bin`, which an interactive shell has
    and launchd does not. Resolving only through PATH would succeed in every
    test and every terminal, and fail in the one place this exists to support.
    """
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    installed = tmp_path / ".local" / "bin" / "phoenix"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")

    found = phoenix_executable()

    assert found == installed
    assert found.is_absolute()


def test_a_missing_collector_names_the_command_that_installs_it(
    tmp_path: Path, monkeypatch
) -> None:
    """A collector that cannot start must say so, not fail somewhere later."""
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    with pytest.raises(FileNotFoundError, match=PHOENIX_PACKAGE):
        phoenix_executable()

    assert "==" in install_command()


def test_the_collector_itself_is_launched_not_a_wrapper() -> None:
    """launchd has to supervise the server, or stopping it does not stop it.

    Running the collector through `uvx` made it a child of the supervised
    process. Measured: a stopped service left the previous collector alive and
    holding port 6006, so the replacement could not bind. The console script
    installed by `uv tool install` is the server, so there is nothing in
    between.
    """
    command = phoenix_command()

    assert Path(command[0]).is_absolute()
    assert Path(command[0]).name == "phoenix"
    assert command[1:] == ["serve"]


def test_the_collector_writes_where_oris_reads(tmp_path: Path) -> None:
    """The trace directory has one definition, and both sides use it.

    It was previously written twice — once in settings and once in a shell
    script, with a comment conceding the two had to be kept in step by hand.
    Deriving the collector's environment from the same settings the reader uses
    makes drift impossible rather than merely discouraged.
    """
    traces = tmp_path / "traces" / "phoenix"
    settings = Settings(
        _env_file=None, PHOENIX_WORKING_DIR=str(traces), **TEST_SETTINGS
    )

    environment = phoenix_environment(settings)

    assert environment["PHOENIX_WORKING_DIR"] == str(traces)
    assert (
        Path(environment["PHOENIX_WORKING_DIR"]) == settings.trace_database_path.parent
    )
    assert traces.is_dir()
    # A local trace viewer, reachable from this machine only, with none of the
    # collector's own outbound features enabled.
    assert environment["PHOENIX_HOST"] == "127.0.0.1"
    assert environment["PHOENIX_TELEMETRY_ENABLED"] == "false"
    assert environment["PHOENIX_ALLOW_EXTERNAL_RESOURCES"] == "false"
    assert environment["PHOENIX_ENABLE_MCP_SERVER"] == "false"
    # Inherited, or `uvx` loses HOME and cannot find its own cache under launchd.
    assert environment["PATH"] == os.environ["PATH"]
