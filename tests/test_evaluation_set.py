"""Tests for the local Web Research evaluation runner."""

import asyncio
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from test_community_research import make_dependencies as community_dependencies
from test_local_knowledge import _graph_answering as local_knowledge_graph_answering
from test_local_knowledge import make_document
from test_threat_intel import iocs
from test_threat_intel import make_dependencies as threat_intel_dependencies
from test_web_research import FakeWebSearch, create_fake_model

from oris.community_research import create_community_research_graph
from oris.evaluation import (
    SPECIALISTS,
    EvaluationCase,
    EvaluationSet,
    load_evaluation_set,
    run_evaluation_cases,
    write_evaluation_report,
)
from oris.search import WebSearchRequest, WebSearchResponse
from oris.threat_intel import ThreatIntelAnswer, create_threat_intel_graph
from oris.web_research import CitedAnswer, create_web_research_graph


class FlakyWebSearch(FakeWebSearch):
    """Answer the first search and fail the second.

    Lets one report hold both outcomes while still running the real graph.
    """

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        if self.requests:
            raise RuntimeError("provider unavailable")
        return await super().search(request)


def test_every_evaluation_set_is_valid_and_runnable() -> None:
    """A case file that exists but cannot be run is worse than no case file.

    Walks the directory rather than naming the files, because the failure worth
    preventing is a new set being added that the runner has no way to drive —
    which a test naming each file by hand would not notice.
    """
    # Routing is deliberately outside this: its cases assert a destination,
    # which is a deterministic pass or fail, and it has no answering graph.
    paths = sorted(
        path
        for path in (Path(__file__).parents[1] / "evaluations").glob("*.toml")
        if path.stem != "routing"
    )

    assert [path.stem for path in paths] == [
        "community_research",
        "local_knowledge",
        "threat_intel",
        "web_research",
    ]
    for path in paths:
        evaluation_set = load_evaluation_set(path)
        assert evaluation_set.specialist == path.stem
        assert evaluation_set.specialist in SPECIALISTS
        assert len({case.id for case in evaluation_set.cases}) == len(
            evaluation_set.cases
        )


def test_routing_evaluation_set_is_valid() -> None:
    """Routing cases use unique IDs and only fixed ORIS routes."""
    evaluation_path = Path(__file__).parents[1] / "evaluations" / "routing.toml"
    with evaluation_path.open("rb") as evaluation_file:
        evaluation = tomllib.load(evaluation_file)

    cases = evaluation["cases"]
    assert evaluation["version"] == 5
    assert len(cases) == 7
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected_route"] for case in cases} == {
        "chat",
        "community_research",
        "local_knowledge",
        "podcast_catch_up",
        "web_research",
    }
    follow_up_case = next(
        case for case in cases if case["id"] == "current-weather-follow-up"
    )
    assert follow_up_case["prior_user_request"]
    assert follow_up_case["prior_assistant_response"]


def _graph_for(specialist: str):
    """Compile one specialist's real graph around scripted dependencies."""
    if specialist == "web_research":
        model, _, _ = create_fake_model(CitedAnswer(answer="A supported answer [1]."))
        return create_web_research_graph(FakeWebSearch(), model)
    if specialist == "local_knowledge":
        graph, _ = local_knowledge_graph_answering(
            "The archive records the decision [1].",
            (make_document(),),
        )
        return graph
    if specialist == "community_research":
        tool, model, _ = community_dependencies()
        return create_community_research_graph(tool, model)
    extract, enrich, lookup, search, model = threat_intel_dependencies(
        iocs(ips=["45.83.192.4"]),
        ThreatIntelAnswer(
            answer="VirusTotal reports 3 detections.",
            sources_used=("45.83.192.4",),
        ),
    )
    return create_threat_intel_graph(extract, enrich, lookup, search, model)


def test_the_runner_can_drive_every_specialist_it_claims_to_know() -> None:
    """Each specialist is asked and read through its own keys, not one guess.

    The runner holds one small table of how each specialist takes a question and
    where its answer and citations live. Those keys differ — `query` against
    `topic` against `request`, `sources` against `cited_urls` against
    `sources_used` — and a wrong one is invisible until a live run fails partway
    through and produces no report at all. Compiled against the production
    graphs for the reason the runner test below gives: a graph double agrees
    with whatever call the runner makes.
    """
    for name in sorted(SPECIALISTS):
        evaluation_set = EvaluationSet(
            version=1,
            specialist=name,
            cases=(
                EvaluationCase(
                    id="probe",
                    category="test",
                    question="What is 45.83.192.4 and the scheduling decision?",
                    evaluation_goal="Exercise the runner's calling convention.",
                ),
            ),
        )

        results = asyncio.run(run_evaluation_cases(_graph_for(name), evaluation_set))

        assert results[0]["status"] == "passed", results[0]["error"]
        assert isinstance(results[0]["answer"], str)
        assert results[0]["answer"]
        assert results[0]["citation_count"] == len(results[0]["citations"])
        assert all(isinstance(citation, str) for citation in results[0]["citations"])


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
        specialist="web_research",
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
    assert results[0]["citation_count"] == 1
    assert results[1]["status"] == "failed"
    assert results[1]["latency_seconds"] == 0.5
    assert results[1]["error"] == "RuntimeError: provider unavailable"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"total": 2, "passed": 1, "failed": 1}
    assert report["model"] == "local-test-model"
