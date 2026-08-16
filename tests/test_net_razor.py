"""Tests for the constrained official Net-Razor MCP connection."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oris.net_razor import (
    NET_RAZOR_SERVER_NAME,
    READ_TIMEOUT,
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
