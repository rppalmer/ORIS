"""Opt-in semantic evaluation for the constrained ORIS router."""

import json
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from oris.chat import create_oris_graph
from oris.config import Settings
from oris.model import create_chat_model

LIVE_ROUTING_ENABLED = os.environ.get("ORIS_RUN_LIVE_ROUTING_TESTS") == "1"
PROJECT_ROOT = Path(__file__).parents[2]
EVALUATION_PATH = PROJECT_ROOT / "evaluations" / "routing.toml"
REPORT_DIRECTORY = PROJECT_ROOT / "artifacts" / "evaluations"
ALLOWED_ROUTES = {
    "chat",
    "community_research",
    "local_knowledge",
    "web_research",
    "youtube_catch_up",
}


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_ROUTING_ENABLED,
    reason="Set ORIS_RUN_LIVE_ROUTING_TESTS=1 to contact oMLX seven times.",
)
def test_router_returns_reviewable_fixed_route_decisions() -> None:
    """The configured model satisfies the fixed routing output schema."""
    with EVALUATION_PATH.open("rb") as evaluation_file:
        evaluation = tomllib.load(evaluation_file)

    settings = Settings()
    graph = create_oris_graph(
        Mock(),
        Mock(),
        Mock(),
        Mock(),
        create_chat_model(settings),
    )
    results = []
    for case in evaluation["cases"]:
        messages = []
        if "prior_user_request" in case:
            messages.extend(
                [
                    HumanMessage(content=case["prior_user_request"]),
                    AIMessage(content=case["prior_assistant_response"]),
                ]
            )
        messages.append(HumanMessage(content=case["request"]))
        graph_result = graph.invoke(
            {
                "messages": messages,
                "mode": "auto",
            },
            interrupt_after="route_request",
        )
        actual_route = graph_result["selected_mode"]
        assert actual_route in ALLOWED_ROUTES
        results.append(
            {
                **case,
                "actual_route": actual_route,
                "resolved_request": graph_result["resolved_request"],
                "matches_expected": actual_route == case["expected_route"],
            }
        )

    generated_at = datetime.now(UTC)
    report = {
        "evaluation_set_version": evaluation["version"],
        "generated_at": generated_at.isoformat(),
        "model": settings.local_llm_model,
        "cases": results,
    }
    REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORT_DIRECTORY / f"routing-{timestamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Routing evaluation report: {report_path}")
