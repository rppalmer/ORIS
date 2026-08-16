"""Tests for the fixed, defensive Threat Intel workflow."""

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from oris.threat_intel import (
    MAX_ENRICHED_INDICATORS,
    REFERENCE_SEARCH_KEY,
    WHOLE_REQUEST,
    ThreatIntelAnswer,
    ThreatIntelPlan,
    build_report,
    create_threat_intel_graph,
)
from oris.threatsyft import THREAT_INTEL_TOOL_NAMES


def envelope(tool_name: str, data: dict) -> dict:
    """Build one ThreatSyft response envelope."""
    return {"ok": True, "tool": tool_name, "query": {}, "data": data, "error": None}


def tool_message(payload: dict) -> ToolMessage:
    """Wrap a ThreatSyft envelope the way the MCP adapter delivers it."""
    return ToolMessage(
        content=json.dumps(payload),
        tool_call_id=str(uuid4()),
        artifact={"structured_content": payload},
    )


def make_tool(name: str, payload: dict | list[dict]) -> Mock:
    """Build one MCP tool returning fixed structured content."""
    tool = Mock(spec=BaseTool)
    tool.name = name
    if isinstance(payload, list):
        tool.ainvoke = AsyncMock(side_effect=[tool_message(item) for item in payload])
    else:
        tool.ainvoke = AsyncMock(return_value=tool_message(payload))
    return tool


def iocs(**kinds: list[str]) -> dict:
    """Build an extraction envelope for the named indicator types."""
    empty = {"ips": [], "domains": [], "urls": [], "hashes": [], "cves": []}
    grouped = {key: [{"value": value} for value in kinds.get(key, [])] for key in empty}
    return envelope("extract_iocs", {"iocs": grouped})


def make_dependencies(
    extraction: dict,
    answer: ThreatIntelAnswer,
    enrich_payloads: dict | list[dict] | None = None,
    plan: ThreatIntelPlan | None = None,
) -> tuple[Mock, Mock, Mock, Mock, Mock]:
    """Build the four approved tools plus planner and synthesis models."""
    extract = make_tool(THREAT_INTEL_TOOL_NAMES[0], extraction)
    enrich = make_tool(
        THREAT_INTEL_TOOL_NAMES[1],
        enrich_payloads
        if enrich_payloads is not None
        else envelope("enrich", {"virustotal": {"malicious": 3}}),
    )
    lookup = make_tool(THREAT_INTEL_TOOL_NAMES[2], envelope("lookup", {"kev": {}}))
    search = make_tool(THREAT_INTEL_TOOL_NAMES[3], envelope("search", {"attack": []}))

    answer_model = Mock()
    answer_model.ainvoke = AsyncMock(return_value=answer)
    planning_model = Mock()
    planning_model.ainvoke = AsyncMock(
        return_value=plan
        or ThreatIntelPlan(capability="both", reference_query="planned terms")
    )
    model = Mock()
    model.with_structured_output.side_effect = lambda schema, **_kwargs: (
        planning_model if schema is ThreatIntelPlan else answer_model
    )
    model.planning_model = planning_model
    model.answer_model = answer_model
    return extract, enrich, lookup, search, model


def test_threat_intel_enriches_indicators_and_looks_up_references() -> None:
    """A `both` plan enriches each indicator and looks up each CVE exactly once."""
    extraction = iocs(ips=["45.83.192.4"], cves=["CVE-2024-1234"])
    extract, enrich, lookup, search, model = make_dependencies(
        extraction,
        ThreatIntelAnswer(
            answer="VirusTotal reports 3 detections for the address.",
            sources_used=("45.83.192.4",),
        ),
        plan=ThreatIntelPlan(capability="both", reference_query="CVE-2024-1234"),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "is 45.83.192.4 in CVE-2024-1234?"}))

    assert result["capability"] == "both"
    assert result["indicators"] == ["45.83.192.4"]
    assert result["sources_used"] == ["45.83.192.4"]
    assert enrich.ainvoke.await_args.args[0]["args"] == {"indicator": "45.83.192.4"}
    assert lookup.ainvoke.await_args.args[0]["args"] == {"reference": "CVE-2024-1234"}
    # The planner supplies concise reference terms rather than the raw sentence.
    assert search.ainvoke.await_args.args[0]["args"]["query"] == "CVE-2024-1234"
    # Provider evidence is full of dates; "last seen 2026-07-14" means nothing
    # to a model that does not know what today is.
    system_message = model.answer_model.ainvoke.await_args.args[0][0][1]
    assert date.today().isoformat() in system_message
    evidence = json.loads(structured_input(model))
    assert set(evidence) == {"45.83.192.4", "CVE-2024-1234", REFERENCE_SEARCH_KEY}


def structured_input(model: Mock) -> str:
    """Return the evidence JSON handed to the synthesis model."""
    human_message = model.answer_model.ainvoke.await_args.args[0][1][1]
    return human_message.split("ThreatSyft evidence:\n", 1)[1]


def test_threat_intel_caps_indicators_per_run() -> None:
    """One request cannot enrich more indicators than the orchestration budget."""
    addresses = [f"10.0.0.{index}" for index in range(MAX_ENRICHED_INDICATORS + 3)]
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=addresses),
        ThreatIntelAnswer(
            answer="No provider reported detections.",
            sources_used=(addresses[0],),
        ),
        enrich_payloads=[envelope("enrich", {"virustotal": {}}) for _ in addresses],
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": " ".join(addresses)}))

    assert len(result["indicators"]) == MAX_ENRICHED_INDICATORS
    assert enrich.ainvoke.await_count == MAX_ENRICHED_INDICATORS


def test_threat_intel_rejects_an_answer_citing_absent_evidence() -> None:
    """The answer may only cite evidence keys ThreatSyft actually returned."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["45.83.192.4"]),
        ThreatIntelAnswer(
            answer="Recorded Future flagged it.",
            sources_used=("recorded_future",),
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    with pytest.raises(ValueError, match="cited unavailable evidence"):
        asyncio.run(graph.ainvoke({"request": "45.83.192.4"}))


def test_enrich_keyword_skips_the_planner_and_the_knowledge_server() -> None:
    """`/threat enrich <indicator>` is deterministic: no planning, no reference calls."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["45.83.192.4"]),
        ThreatIntelAnswer(answer="Three detections.", sources_used=("45.83.192.4",)),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "enrich 45.83.192.4"}))

    assert result["capability"] == "enrich"
    assert result["indicators"] == ["45.83.192.4"]
    model.planning_model.ainvoke.assert_not_awaited()
    enrich.ainvoke.assert_awaited_once()
    search.ainvoke.assert_not_awaited()
    lookup.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("keyword", ["ref", "mitre"])
def test_reference_keyword_skips_the_planner_and_never_enriches(keyword: str) -> None:
    """`/threat ref <term>` reaches only the knowledge server, so nothing egresses."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(),
        ThreatIntelAnswer(
            answer="T1055 covers process injection.",
            sources_used=(REFERENCE_SEARCH_KEY,),
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": f"{keyword} T1055"}))

    assert result["capability"] == "reference"
    assert result["indicators"] == []
    model.planning_model.ainvoke.assert_not_awaited()
    enrich.ainvoke.assert_not_awaited()
    assert search.ainvoke.await_args.args[0]["args"]["query"] == "T1055"


def test_reference_capability_looks_the_query_up_as_well_as_searching_it() -> None:
    """`search` returns trimmed summaries; only `lookup` resolves the full record."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(),
        ThreatIntelAnswer(answer="Actor context.", sources_used=("APT29",)),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    asyncio.run(graph.ainvoke({"request": "ref APT29"}))

    assert lookup.ainvoke.await_args.args[0]["args"] == {"reference": "APT29"}
    evidence = json.loads(structured_input(model))
    assert set(evidence) == {"APT29", REFERENCE_SEARCH_KEY}


def test_freeform_request_uses_the_planner_to_pick_one_capability() -> None:
    """A freeform request is planned, and the plan bounds which tools run."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["45.83.192.4"]),
        ThreatIntelAnswer(
            answer="Process injection context.", sources_used=(REFERENCE_SEARCH_KEY,)
        ),
        plan=ThreatIntelPlan(
            capability="reference", reference_query="process injection"
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "how does process injection work?"}))

    assert result["capability"] == "reference"
    model.planning_model.ainvoke.assert_awaited_once()
    enrich.ainvoke.assert_not_awaited()
    assert search.ainvoke.await_args.args[0]["args"]["query"] == "process injection"


def test_explicit_enrich_without_an_indicator_fails_clearly() -> None:
    """Asking to enrich nothing is an error, not a silent empty investigation."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(),
        ThreatIntelAnswer(answer="unused", sources_used=()),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    with pytest.raises(ValueError, match="No IP address, domain, URL, or file hash"):
        asyncio.run(graph.ainvoke({"request": "enrich not-an-indicator"}))

    enrich.ainvoke.assert_not_awaited()


def test_answers_may_cite_the_provider_inside_an_evidence_entry() -> None:
    """Evidence is keyed by indicator, but the providers under it are real sources.

    Citing "virustotal" rather than the indicator string is the natural reading of
    an attributed answer, and rejecting it failed live investigations that were
    otherwise correct.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(
            answer="Per VirusTotal, no engine flagged it.",
            sources_used=("virustotal", "abuseipdb"),
        ),
        enrich_payloads=envelope(
            "enrich",
            {"sources": {"virustotal": {"ok": True}, "abuseipdb": {"ok": True}}},
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "enrich 8.8.8.8"}))

    assert result["sources_used"] == ["virustotal", "abuseipdb"]


def test_source_status_reports_why_each_provider_did_not_answer() -> None:
    """A missing key and a clean result read identically in prose; not here.

    Per-source failures carry their code at the entry's top level, so the code
    is reported rather than a bare "failed" — "not_found" and "missing_api_key"
    mean very different things to whoever reads the result.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="Mixed results.", sources_used=("virustotal",)),
        enrich_payloads=envelope(
            "enrich",
            {
                "sources": {
                    "virustotal": {"ok": True},
                    "shodan": {"ok": False, "code": "not_found"},
                    "sentinel": {"ok": False, "error": {"code": "missing_api_key"}},
                }
            },
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "enrich 8.8.8.8"}))

    assert result["source_status"] == {
        "virustotal": "ok",
        "shodan": "not_found",
        "sentinel": "missing_api_key",
    }


def test_an_embellished_provider_name_resolves_to_the_real_one() -> None:
    """The local model writes `alienvault_otx` for `alienvault` across runs.

    Observed live: two of three investigations failed on the spelling alone, and
    telling the model not to do it did not stop it. A correct investigation must
    not be discarded over a label, and the output records the canonical name.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["63.106.0.27"]),
        ThreatIntelAnswer(
            answer="Per AlienVault OTX, nothing was reported.",
            sources_used=("alienvault_otx", "VirusTotal"),
        ),
        enrich_payloads=envelope(
            "enrich",
            {"sources": {"alienvault": {"ok": True}, "virustotal": {"ok": True}}},
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "enrich 63.106.0.27"}))

    assert result["sources_used"] == ["alienvault", "virustotal"]


def test_threat_intel_still_rejects_a_provider_that_was_never_consulted() -> None:
    """Broadening the citable set must not stop catching an invented source."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(
            answer="Recorded Future flagged it.",
            sources_used=("recorded_future",),
        ),
        enrich_payloads=envelope("enrich", {"sources": {"virustotal": {"ok": True}}}),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    with pytest.raises(ValueError, match="cited unavailable evidence"):
        asyncio.run(graph.ainvoke({"request": "enrich 8.8.8.8"}))


def test_threat_intel_rejects_an_answer_that_cites_nothing() -> None:
    """Parity with the other specialists: evidence gathered must be evidence cited."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["45.83.192.4"]),
        ThreatIntelAnswer(answer="It looks fine to me.", sources_used=()),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    with pytest.raises(ValueError, match="must cite at least one evidence key"):
        asyncio.run(graph.ainvoke({"request": "enrich 45.83.192.4"}))


def test_evidence_is_stored_even_when_a_summary_was_asked_for() -> None:
    """A summary always prompts "what exactly did that source say".

    Storing only on the report path meant the answer was "re-run it and pay
    again", so collection stores on every path.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="Clean.", sources_used=("8.8.8.8",)),
    )
    saved: dict[str, object] = {}

    class Store:
        retention_days = 30

        def save(self, request, evidence, **_kwargs):
            saved["request"] = request
            saved["evidence"] = evidence
            return SimpleNamespace(report_id="abc123", path=Path("/tmp/r.json"))

    graph = create_threat_intel_graph(
        extract, enrich, lookup, search, model, report_store=Store()
    )

    result = asyncio.run(graph.ainvoke({"request": "enrich 8.8.8.8"}))

    assert result["report_id"] == "abc123"
    assert saved["request"] == "8.8.8.8"
    assert "8.8.8.8" in saved["evidence"]


def test_stored_evidence_names_the_conversation_that_asked_for_it() -> None:
    """Otherwise deleting that conversation cannot find the evidence again.

    The specialist is invoked as a subgraph with no checkpointer of its own, so
    the conversation is passed in rather than inherited from the surrounding
    run's configuration — the link is too important to be ambient.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="Clean.", sources_used=("8.8.8.8",)),
    )
    saved: dict[str, object] = {}

    class Store:
        retention_days = 30

        def save(self, request, evidence, *, thread_id, **_kwargs):
            saved["thread_id"] = thread_id
            return SimpleNamespace(report_id="abc123", path=Path("/tmp/r.json"))

    graph = create_threat_intel_graph(
        extract, enrich, lookup, search, model, report_store=Store()
    )

    asyncio.run(
        graph.ainvoke({"request": "enrich 8.8.8.8", "thread_id": "5a1c-conversation"})
    )

    assert saved["thread_id"] == "5a1c-conversation"


def test_report_keyword_returns_evidence_without_calling_the_model() -> None:
    """The report path is lossless because no model rewrites it.

    Nothing is summarised, so nothing can be dropped or mis-stated in summary.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="unused", sources_used=()),
        enrich_payloads=envelope(
            "enrich",
            {
                "sources": {
                    "virustotal": {"ok": True, "data": {"reputation": 0, "asn": 15169}},
                    "abuseipdb": {"ok": True, "data": {"isp": "Google", "asn": 15169}},
                }
            },
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "report enrich 8.8.8.8"}))

    model.answer_model.ainvoke.assert_not_awaited()
    model.planning_model.ainvoke.assert_not_awaited()
    # Grouped by field, every value still attributed to who said it.
    findings = result["report"]["findings"]["8.8.8.8"]
    assert findings["asn"] == {"virustotal": 15169, "abuseipdb": 15169}
    assert findings["isp"] == {"abuseipdb": "Google"}


def test_a_report_on_two_indicators_keeps_both_answers() -> None:
    """Provider names repeat across indicators, so the subject has to be the key.

    Keyed by field and source alone, the second address's answer overwrites the
    first's and the report reads as one subject with the surviving value.
    """
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["1.1.1.1", "45.83.192.4"]),
        ThreatIntelAnswer(answer="unused", sources_used=()),
        enrich_payloads=[
            envelope(
                "enrich",
                {
                    "sources": {
                        "abuseipdb": {"ok": True, "data": {"confidence_score": 0}},
                        "shodan": {"ok": False, "code": "not_found"},
                    }
                },
            ),
            envelope(
                "enrich",
                {
                    "sources": {
                        "abuseipdb": {"ok": True, "data": {"confidence_score": 100}},
                        "shodan": {"ok": False, "code": "quota_exceeded"},
                    }
                },
            ),
        ],
        plan=ThreatIntelPlan(capability="enrich", reference_query="unused"),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "report enrich these"}))

    report = result["report"]
    assert report["findings"]["1.1.1.1"]["confidence_score"] == {"abuseipdb": 0}
    assert report["findings"]["45.83.192.4"]["confidence_score"] == {"abuseipdb": 100}
    # A source that failed differently on each subject says so on each subject.
    assert report["no_answer"] == {
        "1.1.1.1": {"shodan": "not_found"},
        "45.83.192.4": {"shodan": "quota_exceeded"},
    }
    # The answer line has to name the subject count, or a report covering two
    # indicators reads exactly like one covering a single indicator.
    assert "2 subjects" in result["answer"]


def test_report_keywords_compose_with_a_capability_keyword() -> None:
    """`report enrich <ip>` means both: evidence, and the enrichment path only."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="unused", sources_used=()),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "report enrich 8.8.8.8"}))

    assert result["capability"] == "enrich"
    assert result["report"] is not None
    search.ainvoke.assert_not_awaited()


def test_a_failed_call_is_attributed_to_the_request_not_to_a_source() -> None:
    """A whole call that fails has no source to blame, and must not invent one.

    Falling back to the subject's own name produced entries reading
    `{"not-an-ip": {"not-an-ip": "invalid_indicator"}}`, which a reader takes as
    a provider of that name having failed. A per-source failure still carries
    the provider that reported it, and a successful direct lookup is still
    attributed to the subject, because there the subject genuinely is the
    source.
    """
    report = build_report(
        {
            "8.8.8.8": {
                "ok": True,
                "data": {"sources": {"shodan": {"ok": False, "code": "not_found"}}},
            },
            "not-an-ip!!": {"ok": False, "error": {"code": "invalid_indicator"}},
            "T1059": {"ok": True, "data": {"name": "Command and Scripting"}},
        }
    )

    assert report["no_answer"]["not-an-ip!!"] == {WHOLE_REQUEST: "invalid_indicator"}
    assert report["no_answer"]["8.8.8.8"] == {"shodan": "not_found"}
    assert report["findings"]["T1059"]["name"] == {"T1059": "Command and Scripting"}


def test_report_records_which_sources_had_no_answer() -> None:
    """A silent source and a clean source must not look the same here either."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="unused", sources_used=()),
        enrich_payloads=envelope(
            "enrich",
            {
                "sources": {
                    "virustotal": {"ok": True, "data": {"reputation": 0}},
                    "shodan": {"ok": False, "code": "not_found"},
                }
            },
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    result = asyncio.run(graph.ainvoke({"request": "report enrich 8.8.8.8"}))

    assert result["report"]["no_answer"] == {"8.8.8.8": {"shodan": "not_found"}}
    findings = result["report"]["findings"]["8.8.8.8"]
    assert "shodan" not in findings.get("reputation", {})


def test_report_summarises_bulky_lists_rather_than_inlining_them() -> None:
    """Lists of objects are the bulk of a response and would defeat the pivot."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(ips=["8.8.8.8"]),
        ThreatIntelAnswer(answer="unused", sources_used=()),
        enrich_payloads=envelope(
            "enrich",
            {
                "sources": {
                    "censys": {
                        "ok": True,
                        "data": {
                            "services": [{"port": p} for p in range(22)],
                            "dns_names": [f"h{i}.example" for i in range(40)],
                        },
                    }
                }
            },
        ),
    )
    graph = create_threat_intel_graph(extract, enrich, lookup, search, model)

    findings = asyncio.run(graph.ainvoke({"request": "report enrich 8.8.8.8"}))[
        "report"
    ]["findings"]["8.8.8.8"]

    assert findings["services.count"] == {"censys": 22}
    assert "services" not in findings
    assert len(findings["dns_names"]["censys"]) == 10
    assert findings["dns_names.count"] == {"censys": 40}


def test_threat_intel_requires_its_tools_in_the_approved_order() -> None:
    """A miswired tool set fails at construction, not mid-investigation."""
    extract, enrich, lookup, search, model = make_dependencies(
        iocs(), ThreatIntelAnswer(answer="Nothing found.", sources_used=())
    )

    with pytest.raises(ValueError, match="requires tools in this order"):
        create_threat_intel_graph(enrich, extract, lookup, search, model)
