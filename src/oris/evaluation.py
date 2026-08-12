"""Local, opt-in evaluation runner for the Web Research specialist."""

import json
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from oris.config import NonEmptyString

PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_EVALUATION_PATH = PROJECT_ROOT / "evaluations" / "web_research.toml"
DEFAULT_REPORT_DIRECTORY = PROJECT_ROOT / "artifacts" / "evaluations"


class EvaluationCase(BaseModel):
    """One Web Research question and its human-review goal."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: NonEmptyString
    category: NonEmptyString
    question: NonEmptyString
    evaluation_goal: NonEmptyString


class EvaluationSet(BaseModel):
    """A versioned collection of Web Research evaluation cases."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[2]
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> Self:
        """Keep evaluation case identifiers unambiguous."""
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Evaluation case IDs must be unique")
        return self


def load_evaluation_set(
    path: Path = DEFAULT_EVALUATION_PATH,
) -> EvaluationSet:
    """Load and validate the versioned evaluation cases."""
    with path.open("rb") as evaluation_file:
        values = tomllib.load(evaluation_file)
    return EvaluationSet.model_validate(values)


def run_evaluation_cases(
    graph: CompiledStateGraph,
    evaluation_set: EvaluationSet,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[dict[str, object], ...]:
    """Run every case sequentially and retain failures for the final report."""
    results: list[dict[str, object]] = []
    for case in evaluation_set.cases:
        print(f"Running {case.id}...")
        started_at = clock()
        try:
            graph_result = graph.invoke({"query": case.question})
        except Exception as error:
            result: dict[str, object] = {
                "id": case.id,
                "category": case.category,
                "question": case.question,
                "evaluation_goal": case.evaluation_goal,
                "status": "failed",
                "latency_seconds": round(clock() - started_at, 3),
                "answer": None,
                "source_count": None,
                "sources": [],
                "error": f"{type(error).__name__}: {error}",
            }
        else:
            sources = graph_result["sources"]
            result = {
                "id": case.id,
                "category": case.category,
                "question": case.question,
                "evaluation_goal": case.evaluation_goal,
                "status": "passed",
                "latency_seconds": round(clock() - started_at, 3),
                "answer": graph_result["answer"].answer,
                "source_count": len(sources),
                "sources": [
                    {"title": source.title, "url": str(source.url)}
                    for source in sources
                ],
                "error": None,
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
    report_path = output_directory / f"web-research-{timestamp}.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report_path


def main() -> None:
    """Run the accepted Web Research cases against configured live services."""
    from oris.web_research_app import settings, web_research_graph

    evaluation_set = load_evaluation_set()
    results = run_evaluation_cases(web_research_graph, evaluation_set)
    report_path = write_evaluation_report(
        evaluation_set,
        results,
        model_name=settings.local_llm_model,
    )
    print(f"Report: {report_path}")
    if any(result["status"] == "failed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
