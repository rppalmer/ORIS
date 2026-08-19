"""Tests for the constrained official Net-Razor MCP connection."""

import asyncio
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oris.net_razor import (
    NET_RAZOR_SERVER_NAME,
    NET_RAZOR_TRANSCRIPTION_CEILING,
    READ_TIMEOUT,
    WHISPER_READ_TIMEOUT,
    create_net_razor_client,
    load_community_research_tools,
    load_youtube_catch_up_tools,
)


def test_create_net_razor_client_uses_absolute_stdio_command(tmp_path: Path) -> None:
    """The official client starts the configured Net-Razor interpreter directly."""
    python_executable = tmp_path / "net-razor" / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("test executable", encoding="utf-8")

    client = create_net_razor_client(python_executable)

    assert client.connections == {
        NET_RAZOR_SERVER_NAME: {
            "transport": "stdio",
            "command": str(python_executable),
            "args": ["-m", "net_razor.mcp"],
            # Absent, the MCP SDK skips its own timeout guard and a request
            # waits forever rather than failing the turn.
            "session_kwargs": {"read_timeout_seconds": READ_TIMEOUT},
        }
    }
    assert client.handle_tool_errors is False


def test_create_net_razor_client_rejects_relative_path() -> None:
    """A machine-dependent subprocess path cannot depend on the current directory."""
    with pytest.raises(ValueError, match="absolute path"):
        create_net_razor_client(Path("net-razor/.venv/bin/python"))


def test_load_community_research_tools_keeps_only_allowlisted_tool(
    tmp_path: Path,
) -> None:
    """Unapproved Net-Razor tools do not enter the Community Research boundary."""
    python_executable = tmp_path / "python"
    python_executable.write_text("test executable", encoding="utf-8")
    research_tool = Mock()
    research_tool.name = "net_razor_research"
    unapproved_tool = Mock()
    unapproved_tool.name = "net_razor_run_detail"
    client = Mock()
    client.get_tools = AsyncMock(return_value=[research_tool, unapproved_tool])

    with patch(
        "oris.net_razor.create_net_razor_client",
        return_value=client,
    ):
        tools = asyncio.run(load_community_research_tools(python_executable))

    assert tools == (research_tool,)
    client.get_tools.assert_awaited_once_with(server_name=NET_RAZOR_SERVER_NAME)


def test_load_youtube_catch_up_tools_keeps_only_ordered_allowlist(
    tmp_path: Path,
) -> None:
    """Only the three approved YouTube tools cross the specialist boundary."""
    python_executable = tmp_path / "python"
    python_executable.write_text("test executable", encoding="utf-8")
    transcript_tool = Mock(name="transcript_tool")
    transcript_tool.name = "net_razor_yt_transcript"
    discovery_tool = Mock(name="discovery_tool")
    discovery_tool.name = "net_razor_yt_new_videos"
    acknowledgement_tool = Mock(name="acknowledgement_tool")
    acknowledgement_tool.name = "net_razor_yt_mark_processed"
    unapproved_tool = Mock(name="unapproved_tool")
    unapproved_tool.name = "net_razor_yt_channel_digest"
    client = Mock()
    client.get_tools = AsyncMock(
        return_value=[
            transcript_tool,
            unapproved_tool,
            acknowledgement_tool,
            discovery_tool,
        ]
    )

    with patch(
        "oris.net_razor.create_net_razor_client",
        return_value=client,
    ):
        tools = asyncio.run(load_youtube_catch_up_tools(python_executable))

    assert tools == (discovery_tool, transcript_tool, acknowledgement_tool)
    client.get_tools.assert_awaited_once_with(server_name=NET_RAZOR_SERVER_NAME)


def test_create_net_razor_client_accepts_a_longer_read_timeout(tmp_path: Path) -> None:
    """One tool can be given its own deadline without moving everyone else's.

    Podcast transcription runs for minutes while every other Net-Razor call is
    expected back in seconds. The MCP session timeout is set when the client is
    built and the official adapter never passes a per-call override, so a
    separate deadline means a separate client.
    """
    python_executable = tmp_path / "net-razor" / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("test executable", encoding="utf-8")

    client = create_net_razor_client(
        python_executable,
        read_timeout=WHISPER_READ_TIMEOUT,
    )

    session_kwargs = client.connections[NET_RAZOR_SERVER_NAME]["session_kwargs"]
    assert session_kwargs == {"read_timeout_seconds": WHISPER_READ_TIMEOUT}
    assert create_net_razor_client(python_executable).connections[
        NET_RAZOR_SERVER_NAME
    ]["session_kwargs"] == {"read_timeout_seconds": READ_TIMEOUT}


def test_the_whisper_deadline_clears_net_razors_own_ceiling() -> None:
    """ORIS waits longer than Net-Razor can legitimately take, so it never wins.

    Net-Razor bounds a transcription in three stages — a 30s feed fetch, a 300s
    audio download and a 900s transcriber subprocess — and gives up at 1230s
    with a classified error naming what failed. If ORIS's transport deadline
    fired first it would replace that error with a dead session, discarding the
    one description of the failure anybody has.
    """
    assert WHISPER_READ_TIMEOUT > timedelta(seconds=NET_RAZOR_TRANSCRIPTION_CEILING)
