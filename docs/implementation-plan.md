# ORIS active implementation plan

This file contains only current and future work. Move completed milestones to
[implementation-history.md](implementation-history.md) so this plan remains
short and operational.

Review accepted architecture records when an implementation milestone changes
their assumptions, and update them at that time rather than postponing one
large documentation cleanup. Keep replaceable model names in configuration,
evaluation artifacts, and implementation history unless a specific model is an
actual architectural constraint.

## Goal

Build a private, portable personal assistant whose initial role is web
research. It accepts ad-hoc chat requests and scheduled work, gathers bounded
evidence when a request needs it, produces cited answers with a local
oMLX-hosted model, and keeps workflow activity inspectable locally. Explicit
commands always remain available when the user wants to choose a path directly.

The same Python application must run on the development MacBook or the Mac mini
through configuration changes only. It remains read-only with respect to
external systems.

## Current position

The initial core ORIS milestone has passed its final targeted
acceptance run across direct chat, Web Research, Community Research, YouTube
Catch-up, Local Knowledge, and session isolation.

The parent graph, CLI, durable sessions, explicit Local Knowledge recall, Web
Research, Community Research, and YouTube Catch-up are working. Ordinary CLI
input uses a constrained structured-output router; `/research`, `/community`,
and `/recall` remain deterministic overrides. The router now resolves
context-dependent follow-ups and prepares specialist input in the same model
call. Community Research receives a concise Net-Razor topic; other specialists
receive standalone requests. Failed requests are kept out of conversation history and report
their actual component and reason. Web Research distinguishes current-state
lookups from publication-bounded news and selects Tavily's news category for
explicit news requests. YouTube Catch-up discovers a bounded queue through
Net-Razor, summarizes transcripts one at a time, and maps only its cited digest
and caveats back into chat. It acknowledges successful transcript call IDs only
after final citation validation; Net-Razor remains the sole owner of
processed-video state.

Local Knowledge now plans each archive search into concise terms, a chat or
scheduled-report filter, and relevance or newest ordering. Newest searches
return one document while relevance searches return up to five. Recall answers
are not re-indexed as new knowledge, preventing derived answers from outranking
their original evidence.

Direct chat now leads with the answer, stays concise for ordinary questions,
and expands when the user explicitly requests depth. This is a prompt-only
behavior; no output truncation, graph change, or model configuration was added.

Local Phoenix tracing, the project-owned APScheduler runtime, scheduled run
history, and the transitional `orisctl scheduler` LaunchAgent are working.
The scheduler can run either Web Research or YouTube Catch-up directly from a
validated `schedules.toml` entry. YouTube scheduled runs retain history and a
Markdown report, add the report to Local Knowledge, and only then acknowledge
processed transcripts in Net-Razor. No recurring YouTube job is enabled in the
committed schedule because its timing and work budget have not been chosen.
Deterministic and live verification details are retained in the
[implementation history](implementation-history.md), including the accepted
Community Research, YouTube Catch-up, and the accepted seven-case routing
report.

## Architecture boundaries

- LangGraph owns workflows, state transitions, model calls, and constrained
  routing among the fixed set of approved specialists.
- Data providers and MCP servers own deterministic external data collection.
- No MCP server is part of the ORIS core. The application must start, and every
  capability not backed by that server must work, when it is absent or failing.
  MCP-backed specialists resolve on first use and report unavailability through
  the normal node-failure path. See ADR 001, "MCP independence".
- MCP standardizes tool transport, not capability semantics. Fixed specialists
  depend on ORIS-owned capability boundaries; provider-specific tool names,
  arguments, and result mapping belong in adapters.
- Scheduled jobs invoke a named specialist directly; they do not use an
  interactive chat session or the conversational router.
- Public graph state uses ORIS-owned types. A specialist may retain a
  capability's MCP structured JSON when that server already provides compact,
  normalized evidence; raw upstream provider payloads do not enter graph state.
- LangSmith tracing remains disabled; development traces remain local.
- The router selects exactly one fixed destination. Specialists retain their
  own deterministic paths and tool allowlists.
- No unrestricted model-controlled tool loop or remote-write capability.

## Long-term Mac mini service

- [ ] Move ORIS and oMLX away from dependence on an interactive login.
- [ ] Run the scheduler as a system LaunchDaemon under a dedicated non-root
  service identity.
- [ ] Use service-owned configuration, logs, databases, and artifact paths.
- [ ] Verify a scheduled run after reboot without a user login.
- [ ] Consider LangGraph deployment cron only if persistent Agent Server
  infrastructure later becomes justified.

## Provider and specialist roadmap

- [ ] When a real second backend is available, introduce the smallest
  ORIS-owned adapter needed to preserve the existing specialist contract. Do
  not add provider-selection configuration before two implementations exist.
- [ ] After the fixed workflows remain stable, consider a separate dynamic MCP
  exploration specialist for interactive, read-only, best-effort requests. It
  must use a small explicit tool allowlist and bounded execution, and it must
  not run scheduled or persistence-sensitive workflows.

- [ ] Build the separate Web Evidence MCP server for explicit Tavily or SearXNG
  search, Firecrawl extraction, and bounded Playwright scraping. Keep
  `playwright-stealth` optional and disabled by default. See
  [web-evidence-mcp-plan.md](web-evidence-mcp-plan.md).

The Net-Razor boundary uses the official `langchain-mcp-adapters` package and a
fixed tool allowlist, and Net-Razor is optional configuration. Direct Tavily
access remains until the separate Web Evidence MCP replacement proves equivalent
behavior, tracing, error propagation, and acceptable resource usage. The
Net-Razor-specific tool names and result mapping are still inline in Community
Research and YouTube Catch-up; that is a known deviation from the adapter
boundary, to be repaid when either specialist is next changed rather than
treated as accepted precedent.

## Things to consider later

- Verify that the scheduler LaunchAgent resumes after a real login or reboot
  while the user is logged in.
- Decide whether an always-on Phoenix service belongs on the MacBook or Mac
  mini. If it is needed, give Phoenix its own LaunchAgent and expose its
  lifecycle through `orisctl phoenix <action>`. Do not make scheduler health
  depend on Phoenix health.
- The terminal interface, paused deliberately on 2026-08-12. `uv run oris-tui`
  is an unwired mock: the layout and interactions were accepted, and the
  decision to build it for real waits on a few days of ordinary CLI use. Build
  it only if the cost-beside-the-conversation view turns out to be something
  worth having; if the instinct in the moment is to open Phoenix instead, the
  answer is no and the mock should be deleted rather than left to rot.
  Watch for, while using the CLI: whether the per-turn cost line gets read,
  whether sessions need switching rather than just resuming, and whether the
  twenty-second wait wants streaming and a cancel key.
- Session listing, naming, and switching if the interface demonstrates a need.
  The mock demonstrated it; the data is already in `checkpoints.sqlite` and only
  needs querying. Deletion is the part that needs a decision rather than code:
  what happens to `knowledge.sqlite` rows whose `source_ref` is that thread.
- Selected session deletion with an explicit decision about associated
  searchable knowledge.
- A confirmed administrative backup-and-reset operation for all local state.
- Automatic storage retention limits only after storage growth is measured.
  Prompt-side history trimming is already implemented and is a separate concern.
- Settled 2026-08-10: per-tool-call Net-Razor sessions stay. Measured startup is
  0.30-0.47s, so a five-video YouTube run spends about two seconds launching
  processes against a run dominated by transcript fetches and model calls.
  Holding one session would trade that for a Net-Razor process alive for the
  whole CLI session. Do not revisit without a measurement showing otherwise.
- Settled 2026-08-10: oMLX accepts strict `json_schema` structured output,
  including the `minLength`/`maxLength` constraints ORIS emits, which OpenAI's
  own strict mode rejects. All specialists now use `json_schema`; Community
  Research's `json_mode` was an unexplained inconsistency, not a workaround.
- Keep cross-session recall explicit; do not automatically inject old sessions.
- Provider auto-parameters, advanced search, and an LLM-based evaluator remain
  deferred unless evidence shows they solve a real problem.
- A bounded, defensive ThreatSyft Threat Intel specialist is implemented behind
  the explicit `/threat` command. Broader security-research specialists, n8n,
  and remote-write tools remain outside the current ORIS scope.
- Whether Threat Intel should become a router destination. It is deliberately
  not one today: `enrich` egresses indicators to third-party providers and
  consumes paid credits, so it stays an explicit user choice.

## Immediate next action

The current core milestone is complete. Select the next milestone before adding
more code. Current roadmap candidates are the separate Web Evidence MCP server,
interface improvements, or the later Mac mini service hardening already listed
above. Provider adapters remain contingent on a real second implementation,
and dynamic MCP exploration remains unapproved future work.
