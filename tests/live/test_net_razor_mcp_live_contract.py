"""Opt-in stdio contract for the configured local Net-Razor MCP server."""

import asyncio
import os

import pytest

from oris.config import Settings
from oris.net_razor import load_community_research_tools

RUN_LIVE_TEST = os.getenv("ORIS_RUN_LIVE_NET_RAZOR_TESTS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not RUN_LIVE_TEST,
    reason="Set ORIS_RUN_LIVE_NET_RAZOR_TESTS=1 to start Net-Razor.",
)
def test_net_razor_exposes_community_research_contract() -> None:
    """The configured stdio server exposes the one approved research tool."""
    settings = Settings()
    if settings.net_razor_python_executable is None:
        pytest.fail("NET_RAZOR_PYTHON_EXECUTABLE is required for this live contract")

    tools = asyncio.run(
        load_community_research_tools(settings.net_razor_python_executable)
    )

    assert [tool.name for tool in tools] == ["net_razor_research"]
    input_schema = tools[0].args_schema
    assert isinstance(input_schema, dict)
    assert set(input_schema["properties"]) == {
        "topic",
        "days",
        "sources",
        "max_results_per_source",
    }
