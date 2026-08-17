"""Local, opt-in evaluation runner for ORIS's answering specialists.

A prompt change cannot be judged from one run. The point of this runner is a
report that can be put beside an earlier report on the same fixed questions, so
"the answers got better" is something a person can check rather than assert.

Judging is deliberately left to that person. There is no score here, and no
model grading another model's prose: the report records what was asked, what
came back, what it cited, and how long it took. Read two of them side by side.
"""

import asyncio
import json
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from oris.config import NonEmptyString

PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_EVALUATION_DIRECTORY = PROJECT_ROOT / "evaluations"
DEFAULT_REPORT_DIRECTORY = PROJECT_ROOT / "artifacts" / "evaluations"


@dataclass(frozen=True)
class Specialist:
    """How one specialist is asked a question and where its answer is.

    Specialists answer the same shape of question through different state keys
    and return their citations as different things — a web result, an archive
    document, a URL, a provider name. This is the only place that difference is
    written down, so a new specialist joins the runner by adding an entry rather
    than by growing a branch inside it.
    """

    input_key: str
    read_answer: Callable[[dict[str, Any]], str]
    read_citations: Callable[[dict[str, Any]], list[str]]


SPECIALISTS: dict[str, Specialist] = {
    "web_research": Specialist(
        input_key="query",
        read_answer=lambda result: result["answer"].answer,
        read_citations=lambda result: [
            f"{source.title} — {source.url}" for source in result["sources"]
        ],
    ),
    "local_knowledge": Specialist(
        input_key="query",
        read_answer=lambda result: result["answer"],
        read_citations=lambda result: [
            f"{source.title} ({source.source_type}: {source.source_ref})"
            for source in result["sources"]
        ],
    ),
    "community_research": Specialist(
        input_key="topic",
        read_answer=lambda result: result["answer"],
        read_citations=lambda result: list(result["cited_urls"]),
    ),
    "threat_intel": Specialist(
        input_key="request",
        read_answer=lambda result: result["answer"],
        read_citations=lambda result: list(result["sources_used"]),
    ),
}


class EvaluationCase(BaseModel):
    """One question and the goal a human reads the answer against."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: NonEmptyString
    category: NonEmptyString
    question: NonEmptyString
    evaluation_goal: NonEmptyString


class EvaluationSet(BaseModel):
    """A versioned collection of evaluation cases for one specialist."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    # Not pinned to one number: each specialist's file carries its own, and the
    # version's job is to tell a reader whether two reports asked the same
    # questions. Bump it when the cases change, not when the runner does.
    version: int = Field(ge=1)
    specialist: str
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_set(self) -> Self:
        """Keep case identifiers unambiguous and the specialist runnable."""
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Evaluation case IDs must be unique")
        if self.specialist not in SPECIALISTS:
            raise ValueError(
                f"Unknown specialist: {self.specialist}. "
                f"Known: {', '.join(sorted(SPECIALISTS))}"
            )
        return self


def evaluation_path(
    specialist: str,
    directory: Path = DEFAULT_EVALUATION_DIRECTORY,
) -> Path:
    """Return the case file for one specialist."""
    return directory / f"{specialist}.toml"


def load_evaluation_set(path: Path) -> EvaluationSet:
    """Load and validate one specialist's versioned evaluation cases."""
    with path.open("rb") as evaluation_file:
        values = tomllib.load(evaluation_file)
    return EvaluationSet.model_validate(values)


async def run_evaluation_cases(
    graph: CompiledStateGraph,
    evaluation_set: EvaluationSet,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[dict[str, object], ...]:
    """Run every case sequentially and retain failures for the final report.

    Asynchronous because the graphs are: their evidence nodes await a provider,
    and LangGraph refuses a synchronous `invoke` on a graph that holds an
    asynchronous node. Cases still run one at a time — the point of the report
    is a per-case latency that is comparable across runs.
    """
    specialist = SPECIALISTS[evaluation_set.specialist]
    results: list[dict[str, object]] = []
    for case in evaluation_set.cases:
        print(f"Running {case.id}...")
        started_at = clock()
        asked = {
            "id": case.id,
            "category": case.category,
            "question": case.question,
            "evaluation_goal": case.evaluation_goal,
        }
        try:
            graph_result = await graph.ainvoke({specialist.input_key: case.question})
            citations = specialist.read_citations(graph_result)
            result: dict[str, object] = {
                **asked,
                "status": "passed",
                "latency_seconds": round(clock() - started_at, 3),
                "answer": specialist.read_answer(graph_result),
                "citation_count": len(citations),
                "citations": citations,
                "error": None,
            }
        except Exception as error:
            result = {
                **asked,
                "status": "failed",
                "latency_seconds": round(clock() - started_at, 3),
                "answer": None,
                "citation_count": None,
                "citations": [],
                "error": f"{type(error).__name__}: {error}",
            }
        results.append(result)
        print(
            f"{str(result['status']).upper()} {case.id} ({result['latency_seconds']}s)"
        )
    return tuple(results)


def write_evaluation_report(
    evaluation_set: EvaluationSet,
    results: tuple[dict[str, object], ...],
    *,
    model_name: str,
    output_directory: Path = DEFAULT_REPORT_DIRECTORY,
    generated_at: datetime | None = None,
) -> Path:
    """Write one timestamped JSON report and return its path."""
    report_time = generated_at or datetime.now(UTC)
    passed_count = sum(result["status"] == "passed" for result in results)
    report = {
        "specialist": evaluation_set.specialist,
        "evaluation_set_version": evaluation_set.version,
        "generated_at": report_time.isoformat(),
        "model": model_name,
        "summary": {
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
        },
        "cases": results,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = report_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = evaluation_set.specialist.replace("_", "-")
    report_path = output_directory / f"{name}-{timestamp}.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report_path


async def _build_graph(specialist: str) -> CompiledStateGraph:
    """Compile one specialist against the configured live services."""
    from oris import web_research_app

    if specialist == "web_research":
        return web_research_app.web_research_graph
    if specialist == "local_knowledge":
        return web_research_app.local_knowledge_graph
    if specialist == "community_research":
        return await web_research_app.build_community_research_graph()
    return await web_research_app.build_threat_intel_graph()


async def _main() -> None:
    """Run one specialist's accepted cases against configured live services."""
    specialist = sys.argv[1] if len(sys.argv) > 1 else "web_research"
    if specialist not in SPECIALISTS:
        raise SystemExit(
            f"Unknown specialist: {specialist}. Known: {', '.join(sorted(SPECIALISTS))}"
        )

    evaluation_set = load_evaluation_set(evaluation_path(specialist))
    graph = await _build_graph(specialist)
    from oris.web_research_app import settings

    results = await run_evaluation_cases(graph, evaluation_set)
    report_path = write_evaluation_report(
        evaluation_set,
        results,
        model_name=settings.local_llm_model,
    )
    print(f"Report: {report_path}")
    if any(result["status"] == "failed" for result in results):
        raise SystemExit(1)


def main() -> None:
    """Start the asynchronous evaluation run."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
