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

The initial core ORIS milestone has passed its final targeted acceptance run
across direct chat, Web Research, Community Research, YouTube Catch-up, Local
Knowledge, and session isolation. The parent graph, durable sessions, explicit
Local Knowledge recall, and Threat Intel are working.

Ordinary input uses a constrained structured-output router; `/research`,
`/community`, `/recall`, and `/threat` remain deterministic overrides. The
router resolves context-dependent follow-ups and prepares specialist input in
the same model call. Community Research receives a concise Net-Razor topic;
other specialists receive standalone requests. Failed requests are kept out of
conversation history and report their actual component and reason. Web Research
distinguishes current-state lookups from publication-bounded news and selects
Tavily's news category for explicit news requests. YouTube Catch-up discovers a
bounded queue through Net-Razor, summarizes transcripts one at a time, and maps
only its cited digest and caveats back into chat; Net-Razor remains the sole
owner of processed-video state. Local Knowledge plans each archive search into
concise terms, a chat or scheduled-report filter, and relevance or newest
ordering, and recall answers are not re-indexed as new knowledge. Direct chat
leads with the answer and expands only when depth is requested — a prompt-only
behaviour, with no output truncation or graph change.

There are two front ends over that one graph. The command line is the floor: it
has no optional dependency, works over a plain SSH session, and is what a piped
or scripted run gets. The tabbed terminal interface adds session switching,
deletion, and an activity view built from the traces Phoenix already collected;
`textual` stays an optional extra. Neither owns behaviour the other lacks — the
command vocabulary, the reading of a typed line, the command reference, the
turn runner, and the rule for what gets archived are each defined once and used
by both. Which becomes the primary interface is still open and waiting on real
use.

Local Phoenix tracing, the project-owned APScheduler runtime, scheduled run
history, and the transitional `orisctl scheduler` LaunchAgent are working. The
scheduler can run either Web Research or YouTube Catch-up directly from a
validated `schedules.toml` entry. No recurring YouTube job is enabled in the
committed schedule because its timing and work budget have not been chosen.

Everything ORIS holds as personal data now lives under a fixed `~/.oris`:
configuration, conversation state, the `/recall` archive, stored Threat Intel
evidence, exported activity, and local traces. Those paths used to be relative
and resolved against whatever directory a process started in, so the
interactive session and the scheduler quietly kept separate archives. Each has
an environment override for pointing an existing installation at directories it
already has. Scheduled reports and run history are the remaining exception and
are listed under open questions.

Deterministic and live verification details are retained in the
[implementation history](implementation-history.md), including the accepted
Community Research, YouTube Catch-up, the accepted seven-case routing report,
and the August 13 foundation review and its fixes.

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

## Roadmap

### Answer quality

This is the work that changes what an investigation actually tells the user.
Nothing else on this roadmap moves that number.

- [ ] Review the eleven system prompts in `src/oris/prompts/` as one body of
  work. They come to 140 lines in total, and they — not the graph — decide what
  an answer contains. No pass has yet read them together: the August 13
  foundation review covered code, and pytest cannot assert prompt quality
  because the output is probabilistic. Cover at least what each prompt says
  about citations and about admitting missing evidence, where prompts disagree
  with each other on tone and length, and whether Threat Intel's planning and
  synthesis prompts ask for the analytic structure a real investigation needs.
- [ ] Give the specialists that have no versioned evaluation set one. Today
  there are two: four Web Research questions and seven routing cases. Community
  Research, YouTube Catch-up, Local Knowledge, and Threat Intel have only
  one-off run reports under `artifacts/evaluations/`. A prompt change cannot be
  judged without a before-and-after on the same fixed questions, so this gates
  the review above rather than following it.

### Interfaces

- [ ] Add schedule management to the terminal interface: list the jobs in
  `schedules.toml` with their next run time, show each job's recent run history
  and its reports, and run one on demand. Editing schedules is a separate
  question — `schedules.toml` is version-controlled and project-owned by ADR
  002, and an interface that writes it becomes a second source of truth. Start
  read-only plus a manual trigger, which is what the missed-run diagnosis
  actually needed.
- [ ] Decide whether a long turn needs a cancel key. Per-step status now names
  the running graph node, which was the larger half of the complaint; whether
  the remaining wait is worth interrupting is a question for real use.

### Evidence providers

- [ ] Build the separate Web Evidence MCP server. Its point is to end the
  single-vendor dependency on Tavily and to get past search snippets:
  - **SearXNG** as a second search provider. Self-hosted, so it removes the
    per-search cost and quota from routine research, and it makes provider
    failure a choice rather than an outage. Its recency and domain filters
    depend on the engines it is configured with and must be proven by contract
    test rather than assumed to match Tavily's.
  - **Firecrawl** for page extraction, kept as a separate capability from
    search so a client can review URLs before spending extraction credits.
    Snippets are all Web Research has today; anything needing the body of a
    page cannot currently be answered.
  See [web-evidence-mcp-plan.md](web-evidence-mcp-plan.md) for the tool
  contracts, safety boundaries, and build phases. Keep `playwright-stealth`
  optional and disabled by default.
- [ ] When a real second backend exists, introduce the smallest ORIS-owned
  adapter needed to preserve the existing specialist contract. Do not add
  provider-selection configuration before two implementations exist.
- [ ] After the fixed workflows remain stable, consider a separate dynamic MCP
  exploration specialist for interactive, read-only, best-effort requests. It
  must use a small explicit tool allowlist and bounded execution, and it must
  not run scheduled or persistence-sensitive workflows.

The Net-Razor boundary uses the official `langchain-mcp-adapters` package and a
fixed tool allowlist, and Net-Razor is optional configuration. Direct Tavily
access remains until the Web Evidence MCP replacement proves equivalent
behavior, tracing, error propagation, and acceptable resource usage. The
Net-Razor-specific tool names and result mapping are still inline in Community
Research and YouTube Catch-up; that is a known deviation from the adapter
boundary, to be repaid when either specialist is next changed rather than
treated as accepted precedent.

### Mac mini service

- [ ] Move ORIS and oMLX away from dependence on an interactive login.
- [ ] Run the scheduler as a system LaunchDaemon under a dedicated non-root
  service identity.
- [ ] Use service-owned configuration, logs, databases, and artifact paths.
- [ ] Verify a scheduled run after reboot without a user login.
- [ ] Re-test scheduled execution on the Mac mini once the code is moved there.
  The `weekday-ai-news` job did not fire on the morning of 2026-08-14: the
  scheduler process was alive and had never exited, and no run record or log
  line exists. The cause is that the scheduler is running on a laptop that was
  asleep at the trigger time. APScheduler deliberately skips executions missed
  while it was not running (ADR 002), and a sleeping host is the same case.
  This is a property of the host, not a defect, and it is one of the reasons
  the always-on Mac mini is the intended home. Do not chase it further on the
  MacBook.
- [ ] Consider LangGraph deployment cron only if persistent Agent Server
  infrastructure later becomes justified.

## Open questions

- Scheduled reports and run history still resolve relatively, against whatever
  directory the process started in — `artifacts/scheduled/<job-id>/`, pinned to
  the checkout only because the LaunchAgent sets a working directory. This is
  the same class of problem the `~/.oris` move fixed everywhere else. The
  schedule file and `logs/` are deliberately project-owned and should stay; the
  reports are data and probably should not. Moving them means moving existing
  files, so it needs an explicit decision.
- Which front end is primary. Waiting on real use, not on analysis.
- Verify that the scheduler LaunchAgent resumes after a real login or reboot
  while the user is logged in.
- Decide whether an always-on Phoenix service belongs on the MacBook or Mac
  mini. If it is needed, give Phoenix its own LaunchAgent and expose its
  lifecycle through `orisctl phoenix <action>`. Do not make scheduler health
  depend on Phoenix health.
- A confirmed administrative backup-and-reset operation for all local state.
- The command line's own input history is not covered by session deletion and
  wants its own `/forget`.
- Whether Threat Intel should become a router destination. It is deliberately
  not one today: `enrich` egresses indicators to third-party providers and
  consumes paid credits, so it stays an explicit user choice.
- Keep cross-session recall explicit; do not automatically inject old sessions.
- Provider auto-parameters, advanced search, and an LLM-based evaluator remain
  deferred unless evidence shows they solve a real problem.
- Broader security-research specialists, n8n, and remote-write tools remain
  outside the current ORIS scope.

## Settled — do not revisit without new evidence

Each of these was decided against a measurement or a demonstrated constraint.
They are kept here, rather than in the history file, because they are the
answers to questions that keep getting asked again.

- **Prompt editing from the terminal interface** (2026-08-12). Considered and
  dropped. Viewing is read-only against the trace, which is a fact; editing
  would make the interface a second, unversioned source of prompts.
- **Session deletion removes the conversation and its archive rows**
  (2026-08-12). `SqliteSaver.delete_thread` handles the conversation;
  `KnowledgeRepository.delete_by_source_ref` handles the archive. Phoenix
  traces are deliberately left alone: they belong to Phoenix, which has its own
  retention, and ORIS only reads them. Deletion also removes the Threat Intel
  evidence that conversation collected, matched by the thread recorded in each
  report's filename (2026-08-13).
- **No automatic storage retention** (measured 2026-08-13). Everything ORIS
  owns came to 2.4 MB: 2.2 MB of conversation state across 245 checkpoints in 4
  threads, a 94 KB archive holding 23 documents, and 184 KB of stored evidence.
  Phoenix's own 36 MB dwarfs all of it and already has a 14-day policy. Threat
  reports age out at 30 days. The condition for building automatic retention
  was met and the answer is no: the growth is not there. Revisit when the
  archive passes a few hundred megabytes or the checkpoint database becomes the
  reason a session feels slow. Prompt-side history trimming is already
  implemented and is a separate concern.
- **Per-tool-call Net-Razor sessions stay** (measured 2026-08-10). Startup is
  0.30-0.47s, so a five-video YouTube run spends about two seconds launching
  processes against a run dominated by transcript fetches and model calls.
  Holding one session would trade that for a Net-Razor process alive for the
  whole CLI session. Do not revisit without a measurement showing otherwise.
- **All specialists use strict `json_schema` structured output** (2026-08-10).
  oMLX accepts the `minLength`/`maxLength` constraints ORIS emits, which
  OpenAI's own strict mode rejects. Community Research's `json_mode` was an
  unexplained inconsistency, not a workaround.
- **No node-level timeouts on the search path** (2026-08-13). LangGraph refuses
  a `timeout=` on a synchronous node, so the foundation review's recommendation
  was not implementable as written. The deadline lives in the provider client
  instead. See ADR 001.

## Immediate next action

The core milestone is complete and the August 13 foundation review is closed
out. Nothing in this session's changes has been exercised against live
services; a smoke run over the command line and the terminal interface, plus
the seventeen opt-in live contracts, should come before the next milestone
rather than after it.

Then select one: the prompt and evaluation work under "Answer quality", the
Web Evidence MCP server, or the Mac mini service hardening. Provider adapters
remain contingent on a real second implementation, and dynamic MCP exploration
remains unapproved.
