"""Fixed, defensive Threat Intel workflow backed by ThreatSyft MCP."""

import json
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.prompts import load_system_prompt
from oris.search import NonEmptyText
from oris.threat_reports import ThreatReportStore
from oris.threatsyft import THREAT_INTEL_TOOL_NAMES

THREAT_INTEL_SYSTEM_PROMPT = load_system_prompt("threat_intel_system.txt")
THREAT_INTEL_PLANNING_SYSTEM_PROMPT = load_system_prompt(
    "threat_intel_planning_system.txt"
)

ThreatIntelCapability = Literal["enrich", "reference", "both"]

# Leading keywords that make the capability an explicit user choice instead of a
# planned one. `mitre` is an alias: the reference capability also covers KEV,
# LOLBAS, and NVD, so `ref` is the accurate name for it.
CAPABILITY_KEYWORDS: dict[str, ThreatIntelCapability] = {
    "enrich": "enrich",
    "ref": "reference",
    "mitre": "reference",
}

# Asks for the collected evidence itself rather than a written answer. Composes
# with a capability keyword, so `report enrich <ip>` is both.
REPORT_KEYWORDS = frozenset({"report", "raw", "json"})

# ORIS-owned orchestration budget. ThreatSyft's contract bounds each individual
# call but cannot express a per-run limit across separate calls, so the
# specialist caps how many indicators one request may enrich.
MAX_ENRICHED_INDICATORS = 5
REFERENCE_SEARCH_LIMIT = 5
REFERENCE_SEARCH_KEY = "reference_search"

# The same budget in wall-clock terms. ThreatSyft's session read timeout bounds
# each call, but this node makes one per indicator plus the reference lookups,
# so only the node itself can bound the run. The slowest real single-indicator
# collection took 16 seconds.
EVIDENCE_TIMEOUT_SECONDS = 300

# enrich() accepts network indicators and file hashes; CVEs and technique IDs
# are reference lookups instead.
ENRICHABLE_IOC_TYPES = ("ips", "domains", "urls", "hashes")
REFERENCE_IOC_TYPES = ("cves",)


class ThreatIntelAnswer(BaseModel):
    """Structured local-model response for Threat Intel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: NonEmptyText = Field(
        description="Concise defensive analysis with inline provider attribution."
    )
    sources_used: tuple[NonEmptyText, ...] = Field(
        description="Top-level evidence keys consulted, copied exactly."
    )


class ThreatIntelPlan(BaseModel):
    """One bounded choice of which ThreatSyft capability answers the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: ThreatIntelCapability = Field(
        description="enrich for indicator reputation, reference for defensive context."
    )
    reference_query: NonEmptyText = Field(
        max_length=200,
        description="Concise reference search terms, preserving exact identifiers.",
    )


class ThreatIntelInput(TypedDict):
    """Public input accepted by the Threat Intel graph."""

    request: str
    capability: NotRequired[ThreatIntelCapability]
    report_only: NotRequired[bool]
    # Passed in rather than read from the run's config: the stored evidence has
    # to name the conversation that asked for it so deleting that conversation
    # can take it along, and a link that important should be visible in the
    # call rather than inherited from an ambient context.
    thread_id: NotRequired[str]


class ThreatIntelOutput(TypedDict):
    """Public JSON-compatible output returned by Threat Intel."""

    answer: str
    capability: ThreatIntelCapability
    indicators: list[str]
    sources_used: list[str]
    source_status: dict[str, str]
    report: dict[str, Any] | None
    report_id: str | None
    report_path: str | None


class ThreatIntelState(TypedDict):
    """Internal state shared by Threat Intel nodes."""

    request: str
    capability: NotRequired[ThreatIntelCapability]
    report_only: NotRequired[bool]
    thread_id: NotRequired[str]
    report: NotRequired[dict[str, Any] | None]
    report_id: NotRequired[str | None]
    report_path: NotRequired[str | None]
    reference_query: NotRequired[str]
    indicators: NotRequired[list[str]]
    references: NotRequired[list[str]]
    evidence: NotRequired[dict[str, Any]]
    source_status: NotRequired[dict[str, str]]
    answer: NotRequired[str]
    sources_used: NotRequired[list[str]]


def _structured_content(result: object, tool_name: str) -> dict[str, Any]:
    """Unwrap one ThreatSyft response envelope from its LangChain ToolMessage."""
    if not isinstance(result, ToolMessage):
        raise TypeError(
            f"ThreatSyft {tool_name} did not return a LangChain ToolMessage"
        )
    if not isinstance(result.artifact, dict):
        raise ValueError(f"ThreatSyft {tool_name} did not return structured JSON")
    content = result.artifact.get("structured_content")
    if not isinstance(content, dict):
        raise ValueError(f"ThreatSyft {tool_name} did not return structured JSON")
    return content


MINIMUM_CITATION_STEM = 3

# Report shaping. Values are grouped by subject and then by field rather than by
# source, so the providers that answered the same question about the same
# indicator sit next to each other and disagree visibly. Nothing is judged or
# merged: every value keeps the name of whoever said it and the subject it was
# said about.
REPORT_MAX_DEPTH = 2
REPORT_MAX_LIST_ITEMS = 10
# Bookkeeping a provider adds about itself, not findings about the indicator.
REPORT_SKIP_KEYS = frozenset({"source", "source_url", "note", "ip", "indicator"})


def _flatten_source(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Flatten one source's findings to dotted keys, summarising bulky parts.

    Kept generic rather than keyed to particular providers: ThreatSyft owns what
    each source means, and a report that hardcoded field names here would go
    stale the next time a provider is added.
    """
    flat: dict[str, Any] = {}
    if not isinstance(value, dict):
        return flat
    for key, item in value.items():
        if key in REPORT_SKIP_KEYS:
            continue
        name = f"{prefix}{key}"
        if isinstance(item, dict):
            if depth < REPORT_MAX_DEPTH:
                flat.update(_flatten_source(item, f"{name}.", depth + 1))
            continue
        if isinstance(item, list):
            if not item:
                continue
            if all(isinstance(entry, str | int | float | bool) for entry in item):
                flat[name] = item[:REPORT_MAX_LIST_ITEMS]
                if len(item) > REPORT_MAX_LIST_ITEMS:
                    flat[f"{name}.count"] = len(item)
            else:
                # Lists of objects — Censys services, VirusTotal DNS records —
                # are the bulk of a response. The count says they are there and
                # the raw evidence still holds them.
                flat[f"{name}.count"] = len(item)
            continue
        if item is not None:
            flat[name] = item
    return flat


def build_report(evidence: dict[str, Any]) -> dict[str, Any]:
    """Pivot collected evidence from source-major to subject-major, then field.

    A raw fan-out is one object per provider, which is mostly bulk and hides the
    fact that five sources answered the same question differently. Grouping by
    field puts those answers side by side at a fraction of the size.

    The subject — the indicator or reference the evidence was collected about —
    stays the outer key because provider names repeat across subjects. Without
    it, one indicator's answer overwrites another's, and an address with an
    AbuseIPDB confidence of 0 and one with a confidence of 100 collapse into a
    single row reading 100.
    """
    findings: dict[str, dict[str, dict[str, Any]]] = {}
    errors: dict[str, dict[str, str]] = {}

    for key, envelope in evidence.items():
        if not isinstance(envelope, dict):
            continue
        sources = (envelope.get("data") or {}).get("sources")
        # A lookup answers directly; an enrich answers through a sources map.
        entries = (
            sources
            if isinstance(sources, dict)
            else {key: envelope.get("data") or envelope}
        )
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("ok") is False:
                error = entry.get("error")
                code = entry.get("code")
                if code is None and isinstance(error, dict):
                    code = error.get("code")
                errors.setdefault(key, {})[name] = str(code or "failed")
                continue
            data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
            for field, item in _flatten_source(data).items():
                findings.setdefault(key, {}).setdefault(field, {})[name] = item

    report: dict[str, Any] = {"findings": findings}
    if errors:
        report["no_answer"] = errors
    return report


def _citable_names(evidence: dict[str, Any]) -> set[str]:
    """Return every name an answer may legitimately cite.

    Evidence is keyed by what was asked about — an indicator, a reference, or the
    reference search — but each ThreatSyft envelope carries a `sources` map keyed
    by the provider that answered. Both levels are real sources, and "VirusTotal
    reports" is the more natural citation of the two, so both are citable.
    """
    names = set(evidence)
    for envelope in evidence.values():
        if not isinstance(envelope, dict):
            continue
        sources = (envelope.get("data") or {}).get("sources")
        if isinstance(sources, dict):
            names.update(sources)
    return names


def _source_status(evidence: dict[str, Any]) -> dict[str, str]:
    """Report how each provider fared, using ThreatSyft's own per-source result.

    ThreatSyft answers with every source it tried and why each one failed, which
    is the difference between "nothing was reported" and "nobody was asked". A
    source that succeeded for one indicator and failed for another reports the
    failure, because that is the part a caller would otherwise miss.
    """
    status: dict[str, str] = {}
    for envelope in evidence.values():
        if not isinstance(envelope, dict):
            continue
        sources = (envelope.get("data") or {}).get("sources")
        if not isinstance(sources, dict):
            continue
        for name, entry in sources.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("ok"):
                status.setdefault(name, "ok")
                continue
            # A per-source failure carries its code at the entry's top level;
            # the shared envelope nests one under `error`. Report whichever is
            # present, because "not_found" and "missing_api_key" mean very
            # different things to whoever is reading the result.
            error = entry.get("error")
            code = entry.get("code")
            if code is None and isinstance(error, dict):
                code = error.get("code")
            status[name] = str(code or "failed")
    return status


def _normalise_source(name: str) -> str:
    """Reduce a source name to letters and digits for comparison."""
    return "".join(character for character in name.casefold() if character.isalnum())


def _resolve_citation(citation: str, available: set[str]) -> str | None:
    """Map a cited name onto a real source, tolerating light embellishment.

    A local model reliably names the right provider and unreliably spells it the
    way the data does — `alienvault_otx` for `alienvault` recurs across runs and
    survives being told not to. Rejecting that discards a correct investigation
    over a label, so a citation that resolves to exactly one known source is
    accepted and recorded under the canonical name. A name that matches nothing
    is still a fabrication and still fails.
    """
    wanted = _normalise_source(citation)
    if not wanted:
        return None
    by_normalised = {_normalise_source(name): name for name in available}
    if wanted in by_normalised:
        return by_normalised[wanted]
    if len(wanted) < MINIMUM_CITATION_STEM:
        return None
    matches = {
        canonical
        for normalised, canonical in by_normalised.items()
        if len(normalised) >= MINIMUM_CITATION_STEM
        and (wanted.startswith(normalised) or normalised.startswith(wanted))
    }
    return matches.pop() if len(matches) == 1 else None


def _values(iocs: dict[str, Any], ioc_type: str) -> list[str]:
    """Read one indicator list from ThreatSyft's extraction result."""
    entries = iocs.get(ioc_type)
    if not isinstance(entries, list):
        return []
    return [
        entry["value"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("value"), str)
    ]


def create_threat_intel_graph(
    extract_tool: BaseTool,
    enrich_tool: BaseTool,
    lookup_tool: BaseTool,
    search_tool: BaseTool,
    model: BaseChatModel,
    report_store: ThreatReportStore | None = None,
) -> CompiledStateGraph:
    """Compile a fixed workflow around the approved ThreatSyft tools."""
    actual_tool_names = (
        extract_tool.name,
        enrich_tool.name,
        lookup_tool.name,
        search_tool.name,
    )
    if actual_tool_names != THREAT_INTEL_TOOL_NAMES:
        raise ValueError(
            "Threat Intel requires tools in this order: "
            f"{', '.join(THREAT_INTEL_TOOL_NAMES)}"
        )
    structured_model = model.with_structured_output(
        ThreatIntelAnswer,
        method="json_schema",
    )
    planning_model = model.with_structured_output(
        ThreatIntelPlan,
        method="json_schema",
    )

    async def call_tool(tool: BaseTool, args: dict[str, Any]) -> dict[str, Any]:
        result = await tool.ainvoke(
            {
                "type": "tool_call",
                "id": str(uuid4()),
                "name": tool.name,
                "args": args,
            }
        )
        return _structured_content(result, tool.name)

    def validate_request(state: ThreatIntelState) -> dict[str, object]:
        """Accept the request and honour any explicit leading keywords.

        Keywords are stripped in a loop so they compose: `report enrich <ip>`
        selects the enrichment capability and asks for the evidence rather than
        a written answer.
        """
        request = state["request"].strip()
        if not request:
            raise ValueError("Threat Intel requires a non-empty request")

        updates: dict[str, object] = {}
        while True:
            keyword, _, remainder = request.partition(" ")
            remainder = remainder.strip()
            if not remainder:
                break
            folded = keyword.casefold()
            if folded in CAPABILITY_KEYWORDS and "capability" not in updates:
                updates["capability"] = CAPABILITY_KEYWORDS[folded]
            elif folded in REPORT_KEYWORDS and "report_only" not in updates:
                updates["report_only"] = True
            else:
                break
            request = remainder

        updates["request"] = request
        return updates

    async def plan_investigation(state: ThreatIntelState) -> dict[str, object]:
        """Pick a capability, unless the caller already chose one.

        An explicit choice makes this node deterministic and free: no model call
        happens at all. Only a freeform request is planned, and the planner can
        choose a capability but never an indicator — those are extracted by
        ThreatSyft, so a model cannot cause egress of a value you never supplied.
        """
        if "capability" in state:
            return {"reference_query": state["request"]}
        plan = await planning_model.ainvoke(
            [
                ("system", THREAT_INTEL_PLANNING_SYSTEM_PROMPT),
                ("human", f"Request:\n{state['request']}"),
            ],
            max_completion_tokens=256,
        )
        return {
            "capability": plan.capability,
            "reference_query": plan.reference_query,
        }

    async def extract_indicators(state: ThreatIntelState) -> dict[str, list[str]]:
        extraction = await call_tool(extract_tool, {"text": state["request"]})
        if extraction.get("ok") is not True:
            raise ValueError(
                f"ThreatSyft could not extract indicators: {extraction.get('error')}"
            )
        iocs = (extraction.get("data") or {}).get("iocs")
        if not isinstance(iocs, dict):
            raise ValueError("ThreatSyft did not return an indicator set")

        indicators: list[str] = []
        for ioc_type in ENRICHABLE_IOC_TYPES:
            for value in _values(iocs, ioc_type):
                if value not in indicators:
                    indicators.append(value)
        references: list[str] = []
        for ioc_type in REFERENCE_IOC_TYPES:
            for value in _values(iocs, ioc_type):
                if value not in references:
                    references.append(value)

        budget = MAX_ENRICHED_INDICATORS
        return {
            "indicators": indicators[:budget],
            "references": references[: max(budget - len(indicators[:budget]), 0)],
        }

    async def collect_evidence(state: ThreatIntelState) -> dict[str, object]:
        """Call only the tools the chosen capability needs."""
        capability = state["capability"]
        enriched: list[str] = []
        evidence: dict[str, Any] = {}

        if capability in ("enrich", "both"):
            if capability == "enrich" and not state["indicators"]:
                raise ValueError(
                    "No IP address, domain, URL, or file hash was found to enrich"
                )
            for indicator in state["indicators"]:
                evidence[indicator] = await call_tool(
                    enrich_tool, {"indicator": indicator}
                )
                enriched.append(indicator)

        if capability in ("reference", "both"):
            # Look the query up as well as searching it. `search` returns trimmed
            # summaries, while `lookup` resolves an exact id or name to the full
            # record — for a threat actor that is the difference between a name
            # and the malware and tooling attributed to them.
            references = [state["reference_query"], *state["references"]]
            for reference in dict.fromkeys(references):
                evidence[reference] = await call_tool(
                    lookup_tool, {"reference": reference}
                )
            evidence[REFERENCE_SEARCH_KEY] = await call_tool(
                search_tool,
                {
                    "query": state["reference_query"],
                    "limit": REFERENCE_SEARCH_LIMIT,
                },
            )

        updates: dict[str, object] = {
            "evidence": evidence,
            "indicators": enriched,
            "source_status": _source_status(evidence),
        }
        # Stored on every path, not just the report one. The evidence was
        # collected either way, and a summary always prompts the question the
        # summary cannot answer: what exactly did that source say. Without this
        # the answer is "re-run it and pay again".
        if report_store is not None:
            stored = report_store.save(
                state["request"], evidence, thread_id=state.get("thread_id", "")
            )
            updates["report_id"] = stored.report_id
            updates["report_path"] = str(stored.path)
        return updates

    def compile_report(state: ThreatIntelState) -> dict[str, object]:
        """Return the evidence itself, pivoted, with no model call at all.

        Nothing is summarised, so nothing is lost to summarising, and the answer
        cannot be wrong about the data because no model wrote it. The complete
        provider responses were already written to the store by the collection
        step; they are several times larger than the pivot and would cost
        context on every later turn if they entered the conversation.
        """
        report = build_report(state["evidence"])
        findings = report["findings"]
        # Counted from the pivot itself so the line cannot claim more than the
        # report holds. Subjects are named because a report covering several
        # indicators looked identical to one covering a single indicator.
        fields = sum(len(subject) for subject in findings.values())
        answered = sum(
            1 for status in state["source_status"].values() if status == "ok"
        )
        return {
            "report": report,
            "answer": (
                f"Evidence report for {state['request']}: "
                f"{fields} fields on {len(findings)} subjects from "
                f"{answered} sources."
            ),
            "sources_used": sorted(state["source_status"]),
        }

    def route_after_evidence(state: ThreatIntelState) -> str:
        return "compile_report" if state.get("report_only") else "synthesize_answer"

    async def synthesize_answer(state: ThreatIntelState) -> dict[str, object]:
        response = await structured_model.ainvoke(
            [
                ("system", THREAT_INTEL_SYSTEM_PROMPT),
                (
                    "human",
                    f"Request:\n{state['request']}\n\n"
                    "ThreatSyft evidence:\n"
                    f"{json.dumps(state['evidence'], ensure_ascii=False, indent=2)}",
                ),
            ],
            # This is the only pass over the evidence: the graph returns a
            # summary and the JSON is discarded, so a detail not written here
            # cannot be recovered by a follow-up question.
            max_completion_tokens=1024,
        )
        return {
            "answer": response.answer,
            "sources_used": list(response.sources_used),
        }

    def validate_sources(state: ThreatIntelState) -> dict[str, list[str]]:
        available = _citable_names(state["evidence"])
        resolved: list[str] = []
        unsupported: list[str] = []
        for citation in state["sources_used"]:
            canonical = _resolve_citation(citation, available)
            if canonical is None:
                unsupported.append(citation)
            elif canonical not in resolved:
                resolved.append(canonical)

        if unsupported:
            raise ValueError(
                f"The threat intel answer cited unavailable evidence: "
                f"{sorted(unsupported)}"
            )
        # Parity with the other specialists: an answer built on evidence has to
        # say which evidence. Only the numeric claims inside the prose escape
        # checking, and those are not deterministically verifiable here.
        if available and not resolved:
            raise ValueError(
                "The threat intel answer must cite at least one evidence key"
            )
        # Record what the citations resolved to, so the output names sources the
        # way the data does rather than the way the model happened to spell them.
        return {"sources_used": resolved}

    builder = StateGraph(
        ThreatIntelState,
        input_schema=ThreatIntelInput,
        output_schema=ThreatIntelOutput,
    )
    builder.add_node("validate_request", validate_request)
    builder.add_node("extract_indicators", extract_indicators)
    builder.add_node("plan_investigation", plan_investigation)
    builder.add_node(
        "collect_evidence", collect_evidence, timeout=EVIDENCE_TIMEOUT_SECONDS
    )
    builder.add_node("compile_report", compile_report)
    builder.add_node("synthesize_answer", synthesize_answer)
    builder.add_node("validate_sources", validate_sources)
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "extract_indicators")
    builder.add_edge("extract_indicators", "plan_investigation")
    builder.add_edge("plan_investigation", "collect_evidence")
    builder.add_conditional_edges(
        "collect_evidence",
        route_after_evidence,
        ["compile_report", "synthesize_answer"],
    )
    builder.add_edge("compile_report", END)
    builder.add_edge("synthesize_answer", "validate_sources")
    builder.add_edge("validate_sources", END)
    return builder.compile()
