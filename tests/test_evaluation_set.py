"""Tests for the local Web Research evaluation runner."""

import asyncio
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from test_web_research import FakeWebSearch, create_fake_model

from oris.evaluation import (
    EvaluationCase,
    EvaluationSet,
    load_evaluation_set,
    run_evaluation_cases,
    write_evaluation_report,
)
from oris.search import WebSearchRequest, WebSearchResponse
from oris.web_research import CitedAnswer, create_web_research_graph


class FlakyWebSearch(FakeWebSearch):
    """Answer the first search and fail the second.

    Lets one report hold both outcomes while still running the real graph.
    """

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        if self.requests:
            raise RuntimeError("provider unavailable")
        return await super().search(request)


def test_web_research_evaluation_set_is_valid() -> None:
    """Every evaluation case has a unique ID and complete review metadata."""
    evaluation_path = Path(__file__).parents[1] / "evaluations" / "web_research.toml"
    evaluation_set = load_evaluation_set(evaluation_path)

    assert evaluation_set.version == 2
    assert len(evaluation_set.cases) == 4
    assert len({case.id for case in evaluation_set.cases}) == 4


def test_routing_evaluation_set_is_valid() -> None:
    """Routing cases use unique IDs and only fixed ORIS routes."""
    evaluation_path = Path(__file__).parents[1] / "evaluations" / "routing.toml"
    with evaluation_path.open("rb") as evaluation_file:
        evaluation = tomllib.load(evaluation_file)

    cases = evaluation["cases"]
    assert evaluation["version"] == 4
    assert len(cases) == 7
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected_route"] for case in cases} == {
        "chat",
        "community_research",
        "local_knowledge",
        "web_research",
        "youtube_catch_up",
    }
    follow_up_case = next(
        case for case in cases if case["id"] == "current-weather-follow-up"
    )
    assert follow_up_case["prior_user_request"]
    assert follow_up_case["prior_assistant_response"]


def test_evaluation_runner_drives_the_real_graph(tmp_path: Path) -> None:
    """The runner drives the real graph, and one failure cannot hide a pass.

    Deliberately compiled against the production graph rather than a graph
    double. A double accepts whatever call the runner makes, so it agrees with
    the runner about the calling convention and the result keys no matter what
    either one does — which is how the runner came to be calling a synchronous
    `invoke` on a graph that had gained an asynchronous node.
    """
    evaluation_set = EvaluationSet(
        version=2,
        cases=(
            EvaluationCase(
                id="successful-case",
                category="test",
                question="Successful question?",
                evaluation_goal="Record a validated answer.",
            ),
            EvaluationCase(
                id="failed-case",
                category="test",
                question="Failed question?",
                evaluation_goal="Record a useful failure.",
            ),
        ),
    )
    search = FlakyWebSearch()
    model, _, _ = create_fake_model(CitedAnswer(answer="A supported answer [1]."))
    graph = create_web_research_graph(search, model)
    clock = Mock(side_effect=[10.0, 11.25, 20.0, 20.5])

    results = asyncio.run(run_evaluation_cases(graph, evaluation_set, clock=clock))
    report_path = write_evaluation_report(
        evaluation_set,
        results,
        model_name="local-test-model",
        output_directory=tmp_path,
        generated_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )

    assert len(search.requests) == 1
    assert results[0]["status"] == "passed"
    assert results[0]["latency_seconds"] == 1.25
    assert results[0]["source_count"] == 1
    assert results[1]["status"] == "failed"
    assert results[1]["latency_seconds"] == 0.5
    assert results[1]["error"] == "RuntimeError: provider unavailable"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"total": 2, "passed": 1, "failed": 1}
    assert report["model"] == "local-test-model"
