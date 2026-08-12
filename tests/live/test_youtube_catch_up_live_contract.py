"""Opt-in end-to-end evaluation for the YouTube Catch-up graph."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oris.config import Settings
from oris.model import create_chat_model
from oris.net_razor import load_youtube_catch_up_tools
from oris.youtube_catch_up import create_youtube_catch_up_graph

LIVE_YOUTUBE_CATCH_UP_ENABLED = (
    os.environ.get("ORIS_RUN_LIVE_YOUTUBE_CATCH_UP_TESTS") == "1"
)
REPORT_DIRECTORY = Path(__file__).parents[2] / "artifacts" / "evaluations"


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_YOUTUBE_CATCH_UP_ENABLED,
    reason=(
        "Set ORIS_RUN_LIVE_YOUTUBE_CATCH_UP_TESTS=1 to contact "
        "Net-Razor, YouTube, and oMLX."
    ),
)
def test_youtube_catch_up_completes_one_bounded_live_request() -> None:
    """The configured MCP server and local model complete the fixed graph."""

    async def run_graph() -> tuple[Settings, dict[str, int], dict[str, object]]:
        settings = Settings()
        if settings.net_razor_python_executable is None:
            pytest.fail(
                "NET_RAZOR_PYTHON_EXECUTABLE is required for this live evaluation"
            )

        (
            discovery_tool,
            transcript_tool,
            acknowledgement_tool,
        ) = await load_youtube_catch_up_tools(settings.net_razor_python_executable)
        graph = create_youtube_catch_up_graph(
            discovery_tool,
            transcript_tool,
            acknowledgement_tool,
            create_chat_model(settings),
        )
        request = {"days": 30, "max_videos": 2}
        result = await graph.ainvoke(request)
        return settings, request, result

    settings, request, result = asyncio.run(run_graph())

    assert set(result) == {"answer", "cited_urls", "videos", "caveats"}
    assert isinstance(result["answer"], str)
    assert result["answer"]
    assert isinstance(result["cited_urls"], list)
    assert isinstance(result["videos"], list)
    assert isinstance(result["caveats"], list)
    assert result["videos"], (
        "The live evaluation requires at least one new video from Net-Razor's "
        f"configured channels; caveats: {result['caveats']}"
    )

    generated_at = datetime.now(UTC)
    report = {
        "generated_at": generated_at.isoformat(),
        "model": settings.local_llm_model,
        "request": request,
        "result": result,
    }
    REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORT_DIRECTORY / f"youtube-catch-up-{timestamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"YouTube Catch-up report: {report_path}")
