"""Opt-in end-to-end contract for the Threat Intel graph.

Deliberately exercises only the reference capability. `/threat enrich` sends
indicators to third-party providers and spends API credits, so the default live
contract stays on the local knowledge path; the enrichment path is covered by
ThreatSyft's own tests.
"""

import asyncio
import os

import pytest

from oris.config import Settings
from oris.model import create_chat_model
from oris.threat_intel import create_threat_intel_graph
from oris.threatsyft import THREAT_INTEL_TOOL_NAMES, load_threat_intel_tools

LIVE_THREAT_INTEL_ENABLED = os.environ.get("ORIS_RUN_LIVE_THREAT_INTEL_TESTS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_THREAT_INTEL_ENABLED,
    reason="Set ORIS_RUN_LIVE_THREAT_INTEL_TESTS=1 to contact ThreatSyft and oMLX.",
)
def test_threat_intel_completes_one_local_reference_request() -> None:
    """The configured ThreatSyft servers and oMLX model satisfy the graph contract."""

    async def run_graph() -> dict[str, object]:
        settings = Settings()
        if (
            settings.threatsyft_python_executable is None
            or settings.threatsyft_root is None
        ):
            pytest.fail(
                "THREATSYFT_PYTHON_EXECUTABLE and THREATSYFT_ROOT are required "
                "for this live contract"
            )

        tools = await load_threat_intel_tools(
            settings.threatsyft_python_executable,
            settings.threatsyft_root,
        )
        assert tuple(tool.name for tool in tools) == THREAT_INTEL_TOOL_NAMES

        graph = create_threat_intel_graph(*tools, create_chat_model(settings))
        # The `ref` keyword keeps this deterministic and local: no planning call,
        # and the enrichment server is never reached.
        return await graph.ainvoke({"request": "ref T1055"})

    result = asyncio.run(run_graph())

    assert result["capability"] == "reference"
    # No enrichable indicator in the request, so nothing was sent to a provider.
    assert result["indicators"] == []
    assert result["answer"].strip()
    # Citation validation ran against real evidence rather than a fixture, and
    # every surviving name resolved to a source ThreatSyft actually returned.
    assert result["sources_used"]
