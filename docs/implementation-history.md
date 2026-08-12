# ORIS implementation history

This file preserves the detailed implementation record. The opening sections
capture the July 29, 2026 project snapshot; the dated milestone sections that
follow continue the record through the August 9 core acceptance pass.
References to "next" work in this archive are historical and are not the active
to-do list.

See [implementation-plan.md](implementation-plan.md) for current work.

## Goal

Build a private, portable personal assistant whose initial role is web
research. It will accept a question, collect bounded web evidence, produce a
cited answer with a local oMLX-hosted model, and make every workflow step
inspectable locally.

The same Python application must run on the development MacBook or the Mac mini
through configuration changes only. The first version is read-only: it may
retrieve public information and write local application state, but it may not
modify remote systems.

## Architecture boundaries

- LangGraph owns the research workflow, state transitions, model calls, and
  later specialist routing.
- External providers and MCP servers own deterministic data collection.
- Graph state uses ORIS-owned types rather than provider or MCP
  payloads.
- The initial workflow has fixed edges and no unrestricted model-controlled
  tool loop.
- LangSmith tracing remains disabled. Development traces remain local.
- A root orchestrator will not be built until at least two proven specialist
  workflows require routing.

## Current state

Completed:

- Python 3.12 project managed by `uv`, using a `src/` layout, Ruff, and pytest;
- validated environment configuration with secrets masked;
- official `ChatOpenAI` integration configured for the oMLX OpenAI-compatible
  endpoint;
- opt-in live contracts for chat, streaming, structured output, function
  calling, and one tool round trip;
- official `TavilySearch` integration with fixed basic-search parameters;
- ORIS-owned web-search request, result, and response models;
- Tavily response normalization and an opt-in live search contract;
- a provider-independent `WebSearch` interface;
- a compiled `WebResearch` graph containing `validate_request`, `search_web`,
  structured `synthesize_answer`, and deterministic `validate_answer` nodes
  with fixed edges and distinct input and output schemas;
- a typed cited-answer contract that keeps source URLs application-owned and
  asks the model to reference stable, one-based source numbers;
- a passing opt-in end-to-end Web Research contract using Tavily and oMLX;
- a minimal LangGraph development entry point and `langgraph.json` exposing the
  compiled graph as `web_research`;
- a verified, version-pinned local development-server command that keeps the
  CLI outside the project lock and disables LangSmith tracing, metadata, and
  CLI analytics;
- a verified stateless Agent Server API invocation through `/runs/wait`;
- optional local Phoenix tracing using the official OpenInference LangChain
  instrumentor, with analytics disabled, loopback-only access, local SQLite
  storage, and 14-day retention;
- a verified trace spanning the graph nodes, Tavily tool call, oMLX model call,
  token counts, and a deterministic validation failure;
- a `oris` chat graph using LangGraph's official `MessagesState`
  contract and a fixed conditional route between direct chat and Web Research;
- an explicit `chat` mode that invokes only the local model and an explicit
  `web_research` mode that returns cited sources as links;
- a verified Agent Server schema exposing the `messages` field required by
  compatible clients;
- a local Python command-line chat interface that invokes the compiled
  `oris` graph directly without a separate server or frontend stack;
- verified live Phoenix traces showing direct chat uses only `ChatOpenAI`, while
  explicit Web Research includes one `tavily_search` span and the research
  validation nodes;
- a clean-session manual CLI smoke test in which a UAP research request stayed
  on topic, returned cited sources, and produced a fresh local Phoenix trace;
- a safe, empty project-root `schedules.toml` plus an immutable Pydantic schema
  loaded with standard-library `tomllib`, including time-zone validation,
  duplicate-ID rejection, safe job IDs, and an allowlisted task name;
- durable chat history using the official LangGraph SQLite checkpointer and a
  separate SQLite FTS5 repository containing successful chat exchanges;
- a compiled Local Knowledge specialist that retrieves at most five archive
  documents and asks the model to answer only from that evidence;
- an explicit `/recall <question>` CLI command that selects Local Knowledge
  without model-controlled routing; and
- a passing opt-in Local Knowledge contract using a temporary archive and the
  configured oMLX model, without contacting Tavily;
- a versioned set of four representative Web Research evaluation questions,
  validated during ordinary tests without contacting external services; and
- an explicit local evaluation runner that executes the cases sequentially and
  writes answers, sources, latency, pass/fail status, and errors to one
  timestamped JSON report; and
- a recorded live baseline using `Qwen3.6-35B-A3B-4bit-DWQ`; and
- optional, bounded official-domain and recency controls in the
  provider-independent search request, forwarded through the existing official
  `TavilySearch` integration; and
- a model-backed `SearchPlan` with bounded structured output, hostname
  validation, offline unit tests, and an opt-in local-model contract, integrated
  as one explicit node before the Web Research search node.

Not yet built or approved:

- a user-reviewed acceptance decision for the live evaluation baseline;
- the scheduler runtime and scheduled-report ingestion; or
- MCP integration.

## Step 1: Web Research specialist

The workflow implementation, unit contracts, and opt-in end-to-end Tavily and
oMLX contract are complete.

1. Define a small `WebSearch` interface using the existing ORIS search
   request and response types.
2. Implement a typed `WebResearch` graph with fixed nodes:
   `validate_request -> plan_search -> search_web -> synthesize_answer ->
   validate_answer`.
3. Inject the model and search implementation when constructing the graph.
   Nodes must not read credentials or construct provider clients.
4. Send one validated query to Tavily with the existing bounded configuration.
5. Give the local model only normalized evidence and require source citations.
6. Validate that every citation refers to evidence supplied to the model.

Acceptance criteria:

- blank input fails before an external call;
- one valid request performs exactly one bounded search;
- graph state contains no raw Tavily payload or Tavily SDK object;
- the answer contains only citations tied to normalized results;
- provider and model failures remain visible and useful for diagnosis;
- unit tests use injected fakes and assert state, schemas, and graph paths rather
  than exact model prose;
- the end-to-end live test is opt-in.

## Step 2: Local development visibility and evaluation

After the graph works end to end:

- add `langgraph.json` and run the graph through the local development server;
- inspect graph execution in Studio;
- run Phoenix manually on the MacBook with local storage and a 14-day trace
  retention policy;
- correlate the graph run, Tavily request, and model call with one run ID;
- create a small versioned set of representative research questions;
- record latency, schema validity, citation validity, and failure behavior.

Phoenix must remain optional: stopping it may not prevent the application or
tests from running.

The Agent Server API boundary, local Phoenix trace path, four versioned
evaluation questions, and explicit local runner are complete. The runner reuses
the compiled Web Research graph, continues after individual failures, and
writes one local JSON report.

The first live baseline is recorded in
`artifacts/evaluations/web-research-20260729T120756Z.json`. All four cases passed
the mechanical workflow checks. A preliminary assistant review, not a user or
human acceptance review, found that only the checkpointer-versus-store case met
its full evaluation goal. The LangGraph and SQLite answers were broadly
accurate but did not rely on the requested primary or authoritative sources.
The Python case was stale under either reasonable interpretation: it reported
Python 3.12.3 without a publication date. Later official-source review also
showed that the evaluation question itself is ambiguous. Python 3.12.10 is the
last full maintenance release, while Python 3.12.13 is the newer source-only
security release. The user has not yet reviewed or accepted this baseline.
Mechanical pass status must not be interpreted as factual-quality approval.

A focused Tavily comparison showed that `include_domains` plus a one-year
`time_range` retrieved the correct official LangGraph and Python evidence using
basic search. An official-domain restriction alone returned duplicate SQLite
forum posts. Advanced search cost twice as many credits and returned the same
poor SQLite results; a more precise basic query retrieved SQLite's official WAL
documentation. ORIS therefore keeps basic search and
`auto_parameters=False`. Search controls are available to explicit graph
callers, but ordinary CLI research does not select them automatically.

A second focused comparison tested the original and planner-generated queries
for the three weak baseline cases using six basic Tavily searches. Planning
improved first-party retrieval for two cases: the LangGraph plan surfaced an
official LangChain engineering article, and the SQLite plan surfaced SQLite's
official WAL documentation. The Python 3.12 plan did not improve retrieval; both
queries missed the current maintenance-release page. This limited but material
improvement justified adding `plan_search` to the fixed graph path. The planner
runs once, does not choose the provider or number of searches, and may add a
domain only when the question explicitly requests it. Structured controls
provided directly by a graph caller take precedence, and synthesis continues to
answer the original question rather than the shortened search query.

The first complete evaluation after planner integration is recorded in
`artifacts/evaluations/web-research-20260730T001247Z.json`. All four cases again
passed the mechanical schema and citation-number checks. A preliminary
assistant review found that two cases clearly met their evaluation goals. The
checkpointer comparison cited official LangGraph documentation, and the SQLite
answer cited SQLite's official WAL documentation. The LangGraph-purpose case
improved by retrieving a first-party LangChain article, but cited third-party
sources for its core explanatory claims, so it remains only a partial success.
The Python case is inconclusive because of the evaluation wording. Its answer,
Python 3.12.10 from April 8, 2025, correctly identifies the last full maintenance
release. Python 3.12.13 from March 3, 2026 is newer but is classified as a
source-only security release. The case therefore does not reliably test the
intended question about the newest release in the 3.12 series and must be
rewritten before it can be accepted. The user has not yet reviewed or accepted
this post-integration evaluation.

Evaluation-set version 2 replaces that ambiguous case with a request for the
newest Python 3.12 release including security-only releases, its date, and its
release type. The targeted live result is recorded in
`artifacts/evaluations/web-research-20260730T001726Z.json`. Retrieval found the
official Python 3.12.13 page, and the answer correctly reported Python 3.12.13,
March 3, 2026, and its security-bugfix classification. It did not state that the
release was source-only, so a preliminary assistant review rates it as partial
against the evaluation goal. The question asks generally for the release type,
while the goal specifically requires the source-only detail; that remaining
question-goal mismatch must be resolved before acceptance.

## Step 3: Interactive web-research assistant

Expose the proven `WebResearch` graph through a simple local command-line chat
interface.

The CLI selects the path deterministically: ordinary input uses direct chat and
`/research <question>` uses Web Research. No routing model is allowed to choose
whether an external search occurs.

The two-path messages graph, Agent Server schema, and Python command-line
interface are complete. The CLI invokes the compiled graph directly, while the
same graph remains exposed through Agent Server for future remote clients.
The direct CLI now compiles the parent graph with the official
`langgraph-checkpoint-sqlite` `SqliteSaver`, uses a unique UUID for each
conversation session, and requests synchronous checkpoint durability. A small
pointer file next to the checkpoint database identifies the active session so
it resumes after application restart. `/new` creates and selects a blank
session, while `/session` displays the active ID. Older session checkpoints
remain stored but are not sent to the model. The database path is environment
configurable, and tests prove both checkpoint restoration and active-session
restoration.

CLI failure handling is complete. Unknown slash commands are rejected before
graph invocation. The parent graph uses the official LangGraph node error
handler to convert a model or specialist failure into a short assistant message,
which completes the saved conversation turn and prevents an unanswered user
message from contaminating the next request. The CLI returns to the prompt and
does not add failed exchanges to the searchable knowledge repository. The
underlying failed node remains available in checkpoint and tracing history for
diagnosis. A regression test reproduces the observed failed-research followed
by direct-chat sequence and verifies a valid user/assistant message order.

The repaired CLI and UUID session isolation passed a manual end-to-end check on
July 29, 2026: an explicit UAP research request returned only UAP material with
cited sources, and a new trace appeared in the local Phoenix project. The
legacy `main` checkpoint thread and its contaminated test knowledge document
were then deleted with the user's approval. This confirms the reported
cross-topic contamination is no longer present in retained application data.

### Things to consider for future session management

- List, name, and switch among retained sessions when the CLI or a future chat
  interface demonstrates that navigation is needed.
- Delete a selected session through the official checkpointer
  `delete_thread(session_id)` operation, with an explicit choice about whether
  its searchable knowledge documents should also be removed.
- Add a separate, confirmed administrative reset operation that backs up and
  clears all checkpoints and searchable knowledge. Do not overload `/new` with
  destructive behavior.
- Add a retention policy only after stored checkpoint growth becomes a measured
  problem.
- Keep cross-session recall explicit through `/recall`. Do not inject earlier
  sessions automatically unless a bounded memory policy is later approved.
- Let a future chat client select and retain its own LangGraph thread ID; the
  local pointer file is the simple CLI equivalent of that client behavior.

Conversation restoration and knowledge retrieval are separate. The local
knowledge index is intended to make completed chat turns and final scheduled
reports searchable from chat. It indexes user-facing content and source
metadata, not raw checkpoints, intermediate graph state, or Phoenix traces.

The first knowledge-index contract is complete. `KnowledgeDocument` identifies
either a chat exchange or scheduled run by ID, source reference, timestamp,
title, and user-facing content. `KnowledgeRepository` uses SQLite FTS5 for
durable lexical search with an explicit result limit and optional source-type
filter. It does not implement LangGraph's `BaseStore`, expose backend ranking
scores, require an embedding model, or add a runtime dependency. Tests prove
reopen persistence, replacement by document ID, source filtering, and bounded
retrieval.

CLI chat ingestion and explicit retrieval are complete. After each successful
graph invocation, the CLI writes one knowledge document containing the user's
request and the assistant's user-visible `AIMessage.text`. Failed graph
invocations write nothing. The knowledge database path is environment
configurable. `/recall <question>` retrieves at most five matching documents
through the Local Knowledge specialist, which answers only from that evidence
and returns archive source labels. Scheduled-report ingestion remains unwired.

## Step 4: Scheduled operation and Mac mini deployment

This entire step is deferred until the interactive assistant is explicitly
approved as ready for scheduled operation.

The accepted short-term design uses a project-root `schedules.toml` file as the
source of truth for multiple jobs. A single APScheduler 3.x process will load
and validate the file, recreate its in-memory schedules at startup, and invoke
allowlisted specialist graphs directly. One machine-specific `launchd` service
will supervise that scheduler; individual schedules will not be stored in
launchd plists.

The first service integration will be a per-user LaunchAgent so it can use the
current user-owned project, `.env`, virtual environment, and oMLX DMG. This is a
transitional development arrangement, not the final Mac mini runtime. The
repository will provide a plist template and an idempotent management script
for install, uninstall, restart, and status operations.

Scheduled jobs are unattended deliverables. Successful runs will write
timestamped Markdown files under `artifacts/scheduled/<job-id>/`. Ad-hoc CLI
responses remain terminal output rather than artifacts; their conversation
state is already retained by the official SQLite checkpointer and is not part
of scheduling.

Before enabling scheduled execution, define and test the `schedules.toml`
schema, failure reporting, artifact retention, missed-run policy, safe shutdown,
and one-at-a-time execution. Also resolve how interactive and scheduled model
calls share the single oMLX server.

The `schedules.toml` structure and loader are complete. Cron-expression
semantics remain intentionally unvalidated until APScheduler is added; the
project will use its official `CronTrigger` rather than implementing a parser.

Before the Mac mini runtime is considered always-on, migrate both oMLX and the
ORIS scheduler away from the interactive login session. The scheduler
must run as a system LaunchDaemon under a dedicated non-root service identity,
with service-owned configuration, logs, and artifact directories. Acceptance
requires a successful scheduled run after reboot without a user login. See
[ADR 002](architecture/002-project-owned-scheduling.md).

The long-term deployment path replaces APScheduler and `launchd` with the
official LangGraph cron API when persistent Agent Server infrastructure is
justified. Specialist graph inputs and outputs must remain independent of the
scheduling backend. See
[ADR 002](architecture/002-project-owned-scheduling.md).

## Future research specialists

These are roadmap items, not part of the current build:

- **Web Evidence capability provider:** build a separate, reusable MCP server
  for explicit Tavily or SearXNG search, Firecrawl extraction, and bounded
  manual browser scraping with Playwright. `playwright-stealth` remains an
  optional, disabled-by-default compatibility aid. See the
  [Web Evidence MCP plan](web-evidence-mcp-plan.md).
- **Community Research:** use Net-Razor over stdio MCP to collect normalized,
  audited evidence from X, Hacker News, and optionally topical YouTube search;
  LangGraph will synthesize and cite the evidence.
- **YouTube Catch-up:** use Net-Razor to discover new channel videos, fetch one
  transcript at a time, checkpoint progress, summarize each video, and produce
  a final digest.

Net-Razor remains a separate capability provider. When its first specialist is
approved, use the official `langchain-mcp-adapters` package, expose only the
required MCP tools to that specialist, and add a focused stdio contract test
before integrating it into a graph.

Once a second specialist is proven, add a constrained root chat graph that
routes among a fixed set of specialist names using structured output. Scheduled
jobs will continue to bypass the router and invoke a named specialist directly.

## Deferred or excluded scope

- The Web Evidence MCP server is a separate future project. ORIS keeps
  its direct Tavily adapter until MCP parity is proven. SearXNG, Firecrawl,
  Playwright, and `playwright-stealth` are not ORIS dependencies.
- The local searchable knowledge repository is implemented, populated by
  successful CLI exchanges, and queried explicitly with `/recall`. Scheduled
  runs do not yet populate it. It remains distinct from checkpoint persistence.
- Ad-hoc research artifacts remain deferred. Scheduled Markdown artifacts are
  introduced only as the explicit deliverable of an approved scheduled job.
- Remote-write tools and autonomous side effects are excluded from the current
  read-only assistant.
- ThreatSyft capabilities and security-research specialists are outside the
  ORIS plan.
- n8n is outside the current architecture.

## Immediate next action

The interactive researcher has passed its clean-session readiness check. At the
next development session, decide whether to approve entry into Step 4. If
approved, define one scheduled Web Research job's input, isolated run identity,
Markdown output, failure behavior, and overlap policy before adding the
APScheduler runtime. Provider auto-parameters, advanced search, an unrestricted
tool loop, and an LLM judge remain disabled. Automatic routing and MCP
integration remain deferred.

## Milestones after the July 29 snapshot

### Manual scheduled-job runner — 2026-08-08

- Added `oris-run-scheduled <job-id>` to run one configured Web
  Research job without starting a scheduler.
- Added an isolated UUID run identity and a durable JSON history record that is
  first written as `running`, then completed as `succeeded` or `failed`.
- Added atomic timestamped Markdown reports for successful runs under
  `artifacts/scheduled/<job-id>/`.
- Added scheduled-report ingestion into the existing SQLite FTS5 knowledge
  repository. Failed graph runs create neither a report nor a knowledge entry.
- Kept APScheduler out of the project until the manual vertical slice is
  reviewed.
- Verified the behavior with injected fake graphs: 71 tests passed and 9 live
  tests were skipped. Ruff and package builds also passed.

### First scheduled-job validation — 2026-08-08

- Enabled `weekday-ai-news` in `schedules.toml` for 07:00 Monday through Friday
  in `America/Detroit`.
- The first manual run succeeded operationally and retained JSON run history,
  a Markdown report, and a searchable knowledge document under run ID
  `2c9ce82d-48f9-4609-aea6-34858be5c119`.
- Phoenix trace `c7a99df3f2043eb20efcee5288680d3d` showed the complete fixed graph path and
  no execution errors.
- `/recall` found the scheduled report and answered from it.
- Semantic review did not accept the research result. The planner translated
  "yesterday" to `time_range="year"` and removed the recency term from the
  query, so Tavily returned broad background pages. The synthesizer correctly
  stated that the evidence could not answer the requested timeframe.
- The initial policies retain all scheduled run records and reports until
  measured storage growth justifies limits. A normal future scheduler shutdown
  will stop new starts and wait for an active run to finish.

### Scheduled recency correction — 2026-08-08

- Updated the version-controlled search-planning prompt with general mappings
  for the four supported relative ranges and exact resolution of `today` and
  `yesterday` from the supplied current date.
- A focused live planner call changed the failed plan from a one-year search to
  `query="most important AI-agent developments 2026-08-07"` with
  `time_range="day"`.
- The repeated scheduled run succeeded operationally under run ID
  `6f0895fb-4e32-4b7c-ae26-8f76804b6e65`; Phoenix trace
  `bdb626a45c32fbd5bf2122dfae022bf7` confirmed the corrected request.
- Semantic review still rejected the report. Tavily returned pages published
  or updated inside the relative window, including a weekly aggregator whose
  snippet mixed several dates. The synthesized answer selected August 8,
  August 6, and August 5 items for an August 7 request.
- Official Tavily documentation confirms that the existing LangChain
  integration also accepts absolute `start_date` and `end_date` filters and
  that the `news` topic provides publication-date metadata. Extending the
  provider-independent request contract to use those controls remains a
  separate approval decision.
- A one-credit provider check passed the absolute bounds through the installed
  official integration without error, but Tavily's `general` topic returned
  five generic 2026 overview pages and no `published_date` values. Absolute
  bounds alone are therefore insufficient for this daily-news requirement.

### Exact previous-day news contract — 2026-08-08

- Added explicit `date_window="previous_day"` and
  `search_category="news"` fields to the scheduled job instead of asking
  Tavily to interpret relative language.
- The manual runner derives absolute dates from the configured schedule time
  zone and records the category, start date, and end date in both graph input
  and durable run history.
- Extended the provider-independent search request and graph state with an
  optional category and mutually exclusive relative or absolute date controls.
  Explicit caller controls take precedence over the model-generated plan.
- Configured separate official Tavily wrappers for general and news search.
  The adapter selects the requested category, passes absolute bounds, normalizes
  RFC publication timestamps, and discards missing or out-of-window results.
- Added the executed search request and normalized publication timestamps to
  synthesis evidence. The versioned prompt requires dated claims to use only
  in-window evidence.
- The final manual run succeeded under run ID
  `9998060b-1813-4124-b912-84d01316bce9`. Its four retained sources were all
  published August 7, 2026, and Phoenix trace
  `5fb16fb5765420a5923e6c8682ae06f7` confirmed the complete argument path.
- Verification completed with 75 passing deterministic tests, 9 skipped live
  tests, and a clean Ruff check. Assistant semantic review accepted the date
  accuracy; user acceptance remains the gate before APScheduler is added.

### Local APScheduler runtime — 2026-08-08

- Rechecked current upstream status before implementation. APScheduler 3.11.3
  is production/stable; APScheduler 4 remains an alpha whose maintainers warn
  against production use. LangGraph's official cron API still requires
  LangSmith Deployment infrastructure.
- Added `apscheduler>=3.11.3,<4.0` as a direct runtime dependency. `tzlocal` is
  its only newly locked transitive dependency.
- Added `oris-scheduler`, which validates `schedules.toml`, registers
  only enabled jobs with official `CronTrigger` objects in the configured time
  zone, and calls the proven scheduled-job runner.
- Kept APScheduler's in-memory job store as planned. Schedules are recreated
  from TOML at startup, each job is limited to one active instance, and no file
  watcher or persistent scheduler database was added.
- Added graceful `SIGINT` and `SIGTERM` handling. Shutdown stops the scheduler
  and waits for an active job to complete.
- A real foreground smoke test registered one weekday job in
  `America/Detroit`, made no early research call, and exited with status 0 after
  `Ctrl+C`.
- Verification completed with 78 passing deterministic tests, 9 skipped live
  tests, a clean Ruff check, and successful source and wheel builds.

### Transitional LaunchAgent tooling — 2026-08-08

- Added a repository-owned plist template for the single per-user scheduler
  service. Individual job schedules remain in `schedules.toml`.
- Added the `orisctl scheduler` wrapper with render, install, uninstall,
  start, stop, restart, and status actions. The wrapper uses the current user's
  exact `launchd` domain and the fixed
  `com.rppalmer.oris.scheduler` service label. Stopping retains the
  installed plist; uninstalling removes it.
- The generated plist runs the virtual environment's scheduler executable
  directly, uses the project as its working directory, keeps the process alive,
  classifies it as background work, and separates standard-output and error
  logs under `logs/`.
- Kept secrets out of the plist. The service reads the existing `.env` through
  the application's normal configuration path.
- Rendered the current machine-specific plist under `artifacts/launchd/` and
  validated it with macOS `plutil`. It was deliberately not copied to
  `~/Library/LaunchAgents/` or loaded before user review.
- Focused deterministic verification passed for stable rendering, exact plist
  contents, idempotent removal, and the precise `launchctl` targets used by
  install, start, stop, restart, and status operations. The public command was
  shortened to `orisctl scheduler <action>`. Full verification completed with
  86 passing deterministic tests, 9 skipped live tests, a clean Ruff check, and
  successful source and wheel builds.
- After user approval, installed the plist as the current user's LaunchAgent
  and confirmed that the installed file exactly matched the reviewed artifact.
- Live status showed the expected executable, schedule file, working directory,
  background process classification, log paths, and one running scheduler
  process. The scheduler registered the single enabled job in
  `America/Detroit` without executing it early.
- Verified stop, start, restart, status, uninstall, and reinstall against the
  real service. Each shutdown completed cleanly with exit code 0, each startup
  registered the job once, and the scheduler was left installed and running.

### Unattended scheduled execution — 2026-08-08

- Temporarily scheduled the existing `weekday-ai-news` job for a same-evening
  trigger, restarted through `orisctl`, and confirmed that APScheduler fired at
  the exact configured minute without running early.
- The first trigger retained failed run ID
  `4c3fb670-7ea8-4d54-bb5d-f8b528835217` when the MacBook briefly had no route
  to the configured oMLX host. It failed during search planning, before Tavily,
  retained its error summary, and correctly produced no Markdown report or
  knowledge document.
- A direct endpoint check showed oMLX reachable again. The controlled retry
  fired exactly at 20:24 EDT and succeeded under run ID
  `0d0b8cce-964e-45da-af49-1ab0d786d9b2` in about 15 seconds.
- The successful run retained its JSON history and cited Markdown report and
  added that report to the Local Knowledge index. The report used the explicit
  previous-day range of August 7 through August 8, 2026, end-exclusive.
- Restored `schedules.toml` to `0 7 * * mon-fri`, restarted the LaunchAgent, and
  confirmed that it remained installed and running. No automatic retry was
  added; the retained failure remains the intentional observable behavior.

### Net-Razor MCP stdio contract — 2026-08-08

- Reviewed the current local Net-Razor checkout and its actual MCP server before
  writing ORIS integration code. The server starts with its own virtual
  environment's Python executable and `-m net_razor.mcp` and currently exposes
  eleven tools.
- Added `langchain-mcp-adapters>=0.3.2,<0.4` as a direct dependency. It is the
  official LangChain integration for converting MCP tools into LangChain tools;
  the MCP Python SDK and its transport/schema packages remain transitive.
- Added optional `NET_RAZOR_PYTHON_EXECUTABLE` configuration so the checkout path
  can change between the MacBook and Mac mini without source changes. The local
  `.env` points to the existing Net-Razor virtual environment; the example stays
  machine-neutral.
- Added a small factory that configures the official stateless stdio client. It
  does not reimplement transport, sessions, tool conversion, or retries, and it
  raises MCP execution errors instead of returning them to a model.
- Added a fixed Community Research allowlist containing only
  `net_razor_research`. Audit, diagnostic, individual-source, and YouTube tools
  are discovered by the client but are not returned across this boundary.
- The opt-in live contract started the real Net-Razor server, loaded the one
  approved tool, and verified its `topic`, `days`, `sources`, and
  `max_results_per_source` inputs. It made no upstream provider call.
- Full verification completed with 90 passing deterministic tests, 10 skipped
  opt-in live tests, a separately passing real Net-Razor stdio contract, a clean
  Ruff check, and successful source and wheel builds.

### Fixed Community Research graph — 2026-08-08

- Added a standalone asynchronous LangGraph specialist with one fixed path:
  validate the source boundary, call `net_razor_research` once, then synthesize
  once with the local model.
- Used the official LangChain tool-call interface and the MCP adapter's
  `ToolMessage` artifact. The graph requires `structured_content` and returns
  that JSON unchanged alongside the answer; it does not reimplement MCP or add
  a Net-Razor response-normalization layer.
- Kept the specialist limited to X and Hacker News. Its explicit defaults are a
  one-day window and at most ten results from each selected source. YouTube and
  all other Net-Razor tools remain outside the graph.
- Added a version-controlled Community Research prompt that treats MCP evidence
  as untrusted data and asks the model to cite only supplied canonical URLs.
- Deterministic tests prove the exact graph path, single tool and model calls,
  unchanged JSON handoff, source allowlist, and failure on text-only MCP output.
- Added a deterministic final citation node. When evidence URLs exist, the
  answer must contain at least one Markdown link; every answer URL must exactly
  match a `canonical_url` supplied by Net-Razor. An honest no-evidence answer
  may omit citations. The JSON itself remains unchanged.
- Full verification completed with 97 passing deterministic tests and 10
  skipped opt-in live tests. The changed files are Ruff-formatted and the full
  project is lint-clean. The graph is not yet connected to chat and has not made
  a live upstream call.

### Live Community Research contract — 2026-08-09

- Added a separately gated live contract that loads the real allowlisted MCP
  tool, requests at most three results each from X and Hacker News over a
  30-day window, and makes one synthesis call to the configured local model.
  Ordinary pytest runs continue to skip all external access.
- The contract retains the complete request, answer, model configuration name,
  and unchanged Net-Razor JSON under `artifacts/evaluations/` for human review.
- The first live run passed its hard contracts and collected three results from
  each source, but semantic review rejected the answer because it reached the
  512-token generation ceiling mid-bullet. This was a prompt-length problem,
  not an MCP, provider, or citation failure.
- Limited the version-controlled prompt to 250 words and five concise bullets
  without increasing the token budget. The repeated run completed with five
  bullets and six valid evidence links; assistant semantic review accepted it
  as a community-signal summary.
- The accepted report is
  `artifacts/evaluations/community-research-20260809T023516Z.json`, backed by
  Net-Razor research call `fa9bd7855537426e929fe55e7ddd0eba`.
- Verification completed with 97 passing deterministic tests, 11 skipped
  opt-in live tests, a separately passing live Community Research contract, and
  a clean Ruff lint check.

### Structured Community Research, CLI integration, and routing — 2026-08-09

- Replaced Markdown parsing with the official model structured-output wrapper.
  Community Research now returns `answer`, `cited_urls`, and the unchanged
  `research_result`; its final node validates every cited URL against the MCP
  evidence's canonical URLs.
- Connected the asynchronous Community Research specialist to the parent graph
  with the documented wrapper-node pattern for graphs whose state schemas
  differ. Added official async graph factories for the LangGraph development
  server and exposed `community_research` as a direct graph ID.
- Converted the CLI to async invocation and the official `AsyncSqliteSaver`.
  This used the existing `langgraph-checkpoint-sqlite` dependency and its
  existing transitive `aiosqlite` dependency; no runtime dependency was added.
- Added `/community <topic>` as an explicit override. It performs one fixed
  Net-Razor request for X and Hacker News over one day with at most ten results
  per source, followed by one structured synthesis call.
- Added a constrained structured-output parent router for ordinary CLI input.
  It can choose only direct chat, Web Research, Community Research, or Local
  Knowledge. Explicit slash commands bypass it, and routing failures close the
  request rather than selecting a fallback.
- Added a versioned five-case routing evaluation. Its live report,
  `artifacts/evaluations/routing-20260809T030416Z.json`, matched all five intended
  destinations; exact semantic matches remain review results rather than
  blocking pytest contracts.
- The revised structured Community Research live contract passed and retained
  `artifacts/evaluations/community-research-20260809T030348Z.json`. The complete
  `/community` CLI path also passed against the real MCP server and local model,
  including async checkpoint restoration and Local Knowledge indexing.
- The first CLI live-test attempt exposed only a pytest stream-capture
  incompatibility with the MCP subprocess. Switching that test from `capsys` to
  file-descriptor capture fixed the harness without changing production code.
- Full deterministic verification completed with 105 passing tests and 13
  skipped opt-in live tests, plus a clean Ruff check.

### YouTube Catch-up contract — 2026-08-09

- Consulted the official LangGraph graph, subgraph, and structured-output
  documentation before defining the specialist boundary.
- Inspected the current Net-Razor implementation and verified the live MCP
  schemas for `net_razor_yt_new_videos` and `net_razor_yt_transcript` without
  contacting YouTube.
- Selected Net-Razor's recommended incremental workflow: one compact discovery
  call followed by one bounded transcript at a time. The bulk channel-digest
  tool is explicitly excluded because its combined transcript output can exceed
  the MCP host's limit.
- Defined a five-video default and ten-video hard ceiling for ORIS's
  total model-work budget, plus sequential processing, structured per-video
  summaries, a final digest, exact citation validation, and no full transcripts
  in public output.
- Removed proposed ORIS copies of Net-Razor's day, per-channel,
  language, and transcript-length rules. Net-Razor remains authoritative for
  provider behavior; ORIS will not compensate for missing MCP
  capabilities.
- Recorded the known gap between Net-Razor marking a transcript processed and
  ORIS completing its summary. Net-Razor must provide suitable
  processing or replay semantics before scheduled unattended use;
  ORIS will not compensate with a second queue or deduplication store.
- The complete contract is in
  [youtube-catch-up-contract.md](youtube-catch-up-contract.md).

### YouTube Catch-up MCP allowlist — 2026-08-09

- Added a separate ordered allowlist containing only
  `net_razor_yt_new_videos` and `net_razor_yt_transcript`; the future specialist
  cannot receive Net-Razor's bulk digest or unrelated tools.
- Reused the existing official `MultiServerMCPClient` and one small shared
  filtering function. No dependency, transport wrapper, schema copy, provider
  validation, or graph code was added.
- Added one unit test proving that unrelated tools are excluded and the two
  approved tools are returned in workflow order.
- Removed the proposed live YouTube schema test before running it. Runtime MCP
  discovery and the future specialist's normal integration test will cover real
  compatibility without mirroring Net-Razor's schema and defaults.
- Verification completed with 106 passing deterministic tests, 13 skipped
  opt-in live tests, and clean Ruff formatting and lint checks.

### Standalone YouTube Catch-up graph — 2026-08-09

- Added a standalone asynchronous `StateGraph` with four fixed nodes: discover
  videos, summarize them sequentially, create one digest, and validate final
  citations.
- The graph uses only the approved incremental Net-Razor tools. It requests one
  compact video queue and then one transcript at a time; it does not receive or
  call the bulk channel-digest tool.
- ORIS enforces only its total model-work budget of five videos by
  default and ten maximum. Net-Razor remains authoritative for provider limits,
  language selection, transcript length, and provider-side processing state.
- Full transcripts remain local to the sequential summary node and do not enter
  graph state, public output, or Local Knowledge. Final synthesis sees only the
  small per-video summaries.
- Added separate version-controlled prompts for one-video summaries and the
  final digest, both using the existing official structured-output model
  wrapper.
- No scheduler, parent router, CLI, retry, custom checkpoint, new dependency,
  or live provider call was added. The graph compiled successfully, the existing
  106 deterministic tests still passed, and Ruff checks were clean; focused
  behavioral acceptance remains the next milestone.

### YouTube Catch-up deterministic acceptance — 2026-08-09

- Added five focused tests covering a bounded multi-video run, sequential
  transcript calls, combined summary input, an empty queue, an unavailable
  transcript, an invalid total budget, and an unsupported citation.
- The combined-digest test proves that the final model receives all successful
  per-video summaries but no full transcript text. Updated the prompts to retain
  important claims in each summary and connect related or conflicting videos in
  the final digest.
- Full deterministic verification completed with 111 passing tests, 14 skipped
  opt-in live tests, and clean Ruff formatting and lint checks.
- The first live evaluation passed the wiring path but found no configured
  `YOUTUBE_CHANNEL_IDS` in Net-Razor. It returned no videos, made no transcript
  or model calls, and retained
  `artifacts/evaluations/youtube-catch-up-20260809T043545Z.json`.
- That empty run is not semantic acceptance. The opt-in evaluation now requires
  at least one summarized video and must be rerun after Net-Razor has a
  configured channel.

### YouTube Catch-up live acceptance — 2026-08-09

- After `YOUTUBE_CHANNEL_IDS` was configured, the live evaluation resolved five
  channels, discovered three new videos, and processed the configured maximum
  of two videos sequentially.
- Both transcript calls succeeded. One transcript was complete and one was
  truncated by Net-Razor's transcript limit; both produced an individual
  summary and appeared in the final digest.
- The two videos covered unrelated subjects. The model correctly kept them
  separate instead of inventing a connection, and both returned citation URLs
  exactly matched the summarized videos.
- The run exposed one deterministic presentation gap: transcript truncation was
  present in video metadata but absent from the top-level caveats. The graph now
  always adds a truncation caveat from Net-Razor's boolean flag, with a focused
  pytest assertion; it does not rely on the model to mention that limitation.
- The accepted live report is
  `artifacts/evaluations/youtube-catch-up-20260809T044149Z.json`. Full local
  verification completed with 111 passing tests, 14 skipped opt-in live tests,
  and a clean Ruff check.

### YouTube Catch-up parent integration — 2026-08-09

- Consulted the official LangGraph subgraph documentation and used its wrapper
  node pattern because the parent chat graph and YouTube specialist have
  different state schemas.
- Added `youtube_catch_up` as one fixed parent destination. The wrapper invokes
  the accepted specialist with its existing defaults and maps only its digest,
  canonical source URLs, and caveats into one assistant message; transcript text
  never enters parent conversation state.
- Extended the constrained router schema and version-controlled prompt with the
  new destination. Added one corresponding case to routing evaluation version
  2, but did not run that semantic evaluation in this milestone.
- The application factory now loads the two approved YouTube MCP tools and
  compiles the specialist when it builds the complete parent graph. No runtime
  dependency, scheduler behavior, dedicated CLI command, or new tool permission
  was added.
- Deterministic verification completed with 112 passing tests, 14 skipped
  opt-in live tests, and a clean Ruff check.

### YouTube Catch-up routing acceptance — 2026-08-09

- Ran routing evaluation version 2 against the configured local model. It made
  six model calls and stopped after each route decision, without invoking a
  specialist, MCP tool, Tavily, or YouTube.
- The report's automatic comparison marked all six expected destinations as
  exact matches: two direct-chat cases plus Local Knowledge, Web Research,
  Community Research, and YouTube Catch-up. Codex inspected the report; the
  user has not reviewed it.
- The retained report is
  `artifacts/evaluations/routing-20260809T050841Z.json`. Pytest acceptance proves
  every result is one allowed route; the reviewed 6/6 semantic match remains an
  evaluation result rather than a brittle blocking assertion.

### Explicit YouTube processing acknowledgement — 2026-08-09

- Reviewed the local Net-Razor implementation and confirmed that discovery had
  treated any successful transcript audit item as completed downstream work.
  This could hide a video when ORIS failed during later synthesis.
- Added explicit processed-video state to Net-Razor and an audited,
  all-or-nothing, idempotent `net_razor_yt_mark_processed` MCP tool. It accepts
  successful transcript call IDs as receipts. A one-time upgrade preserves
  legacy processed IDs, while later audit pruning cannot erase processing state.
- Net-Razor discovery now excludes only explicitly acknowledged videos. A
  transcript fetch alone remains discoverable. Captionless videos retain their
  existing behavior and recur until they leave the recent-feed window.
- Expanded ORIS's narrow YouTube allowlist to three tools. The graph
  retains successful transcript call IDs only in internal state and invokes the
  acknowledgement tool once after final citation validation. Empty results,
  transcript failures, digest failures, and citation failures do not
  acknowledge work.
- The real stdio adapter exposed exactly the three expected YouTube tools without
  contacting YouTube. Net-Razor verification completed with 89 passing tests and
  clean Ruff lint; ORIS completed with 113 passing tests, 14 skipped
  opt-in live tests, clean Ruff lint, and formatting checks on all changed Python
  files.
- Scheduled YouTube use remains a separate milestone because durable report
  persistence and acknowledgement ordering must be defined before extending the
  scheduler.

### Scheduled YouTube contract — 2026-08-09

- Consulted the official LangGraph Graph API, persistence, fault-tolerance, and
  subgraph guidance. The contract keeps processing receipts outside
  user-facing output and treats acknowledgement as an idempotent side effect
  whose order must remain explicit.
- Defined the task-specific scheduled input as explicit `days` and
  `max_videos`, without a natural-language prompt or duplicated Net-Razor
  settings.
- Defined a timestamped Markdown deliverable containing the final digest,
  individual video summaries, sources, caveats, and truncation status, while
  excluding transcripts, raw MCP results, and processing receipts.
- Chose the fixed completion order: create and validate the result, atomically
  write and index the report, acknowledge Net-Razor transcript call IDs, then
  mark the run successful.
- Defined acknowledgement failure as a failed run with its completed report
  retained. This accepts possible duplicate reporting rather than risk marking
  videos processed without a durable report.
- Recorded that filesystem, knowledge, history, and Net-Razor writes do not
  share an atomic transaction. Automated retries, crash reconciliation, and a
  second copy of the specialist workflow remain out of scope.

### Scheduled YouTube configuration — 2026-08-09

- Added separate validated schedule types for Web Research and YouTube Catch-up
  while retaining their shared job ID, enabled flag, and cron expression.
- A YouTube schedule requires explicit `days` and `max_videos` and rejects
  fields that belong to Web Research. Provider-owned channel, language, and
  transcript-limit settings were not duplicated in ORIS.
- Kept the committed `schedules.toml` unchanged. YouTube scheduled execution,
  report writing, and acknowledgement ordering were not implemented in this
  step.
- Full deterministic verification completed with 116 passing tests, 14 skipped
  opt-in live tests, and clean Ruff formatting and lint checks.

### Scheduled YouTube execution — 2026-08-09

- Split the accepted YouTube workflow at its completion boundary without
  duplicating research logic. One preparation graph performs discovery,
  transcript retrieval, summaries, digest creation, and citation validation;
  interactive and scheduled wrappers decide when to acknowledge Net-Razor.
- Added scheduled dispatch for `task = "youtube_catch_up"`. Each run records its
  explicit `days` and `max_videos`, writes an atomic Markdown report, adds that
  report to Local Knowledge, acknowledges successful transcript receipts, and
  then marks the run successful.
- Added deterministic coverage for schedule registration, exact job inputs,
  report contents and exclusions, persistence-before-acknowledgement ordering,
  empty results, preparation failure, and acknowledgement failure after a
  report is retained.
- The complete deterministic suite passed with 122 tests and 15 skipped opt-in
  live tests. Ruff lint and formatting checks passed for all 53 Python files.
- The first sandboxed live attempt could not write Net-Razor's external log and
  correctly retained a failed run record at
  `artifacts/scheduled/youtube-catch-up-live/20260809T154932Z-89da51df-1c8c-4f70-9116-0afb22a37ee5.json`.
  A normal-permission rerun succeeded and retained its report and history at
  `artifacts/scheduled/youtube-catch-up-live/20260809T160359Z-1ceaae72-06c5-446f-bb76-42affdd8e7a0.md`
  and the matching `.json` file.
- That live run found recent videos but the selected video had transcripts
  disabled, so the successful report contains the handled caveat and no sources
  or acknowledgement. This validates the real empty-transcript path; the
  deterministic suite protects the acknowledgement ordering. Codex inspected
  the artifacts; the user has not reviewed them.
- The committed `schedules.toml` remains unchanged. No recurring YouTube job was
  invented or enabled on the user's behalf.

### Conversational handoff and current-state research correction — 2026-08-09

- An end-to-end acceptance pass exposed two separate defects. The parent sent
  only the newest message to a specialist, so a supplied ZIP code lost its
  preceding weather question. A strict publication-date rule then rejected
  valid current-weather evidence because weather pages lacked article
  publication metadata.
- Consulted the official LangGraph graph, persistence, messages, and short-term
  memory documentation. Extended the existing structured routing result with a
  standalone resolved request and passed relevant conversation messages to that
  same model call. No additional routing call, tool loop, dependency, or custom
  memory system was added.
- Added a version-controlled direct-chat prompt for truthful capability limits
  and concise clarification questions. Explicit slash commands still bypass
  routing and use their supplied request unchanged.
- Changed handled node failures to remove the failed user turn with
  `RemoveMessage` and retain the failed component and reason in non-message
  state. The CLI displays that reason, while later model context and Local
  Knowledge do not receive a synthetic error response. Older copies of the
  former standard failure turn are filtered from model input.
- Updated search planning so provider date filters represent publication
  recency. Current or historical conditions such as weather keep the resolved
  date in the query without requiring `published_date`; strict scheduled-news
  filtering remains unchanged.
- Deterministic verification completed with 124 passing tests, 15 skipped
  opt-in live tests, and clean Ruff lint and formatting checks.
- The focused live planner check passed both an explicit publication-recency
  plan and a current-weather plan with no publication bounds. Routing evaluation
  version 3 matched all seven expected destinations and resolved the weather
  follow-up to `What is the weather today in ZIP code 48383?`; its report is
  `artifacts/evaluations/routing-20260809T164052Z.json`. Codex inspected the
  report; the user has not reviewed it.
- One exact end-to-end Web Research retry succeeded with five cited weather
  sources, including the National Weather Service. The initial sandboxed live
  planner attempt was blocked before reaching oMLX; the normal-permission rerun
  passed and is the accepted result.

### Ad-hoc news category selection — 2026-08-09

- An explicit `/research` request for yesterday's AI-agent news produced the
  correct absolute publication window but used Tavily's general category. Its
  five results had no `published_date`, so the existing strict date check
  correctly rejected them.
- Added `search_category` to the existing structured search plan using the
  already supported `general` and `news` values. An explicit caller category,
  including scheduled Web Research's fixed `news` category, still takes
  precedence over the plan.
- Added no graph node, retry, fallback, keyword rule, provider abstraction, or
  dependency. Focused deterministic verification proved planned-category
  propagation and explicit-category precedence.
- The exact local-model plan selected `news` with the August 8–9 bounds. The
  end-to-end retry then returned four sources, all published August 8, and a
  cited answer. The existing multi-case live planner check separately produced
  conflicting weekly and absolute controls on one older case; the schema
  rejected that invalid model output, and no recovery machinery was added for
  the isolated semantic inconsistency.
- Full deterministic verification completed with 126 passing tests, 16 skipped
  opt-in live tests, and clean Ruff lint and formatting checks.

### Community topic preparation — 2026-08-09

- The end-to-end acceptance run showed that Net-Razor returned no results when
  Community Research received the full instruction `What are people on X and
  Hacker News saying about LangGraph?`, while the concise topic `LangGraph`
  returned ten X results.
- Consulted the official router guidance, which allows the routing step to
  decompose a request and pass a destination-specific query to a specialist.
  Updated the existing structured router prompt so Community Research receives
  only its concise topic. No graph node, model call, parser, dependency, or
  Net-Razor behavior was added.
- Explicit `/community <topic>` remains a deterministic override and continues
  to pass the supplied topic unchanged.
- Routing evaluation version 4 matched all seven expected destinations and
  produced `LangGraph` for the Community topic. The report is
  `artifacts/evaluations/routing-20260809T230144Z.json`.
- An isolated ordinary-chat request then sent `LangGraph` to the real Net-Razor
  MCP server, received ten X results and no Hacker News results, and returned
  five cited X sources. The answer ended in the middle of a sentence and was
  recorded for separate review.
- Phoenix later confirmed that this response stopped normally after 287 output
  tokens, below the 512-token ceiling. The ceiling did not truncate the answer.
- Full deterministic verification completed with 126 passing tests, 16 skipped
  opt-in live tests, and clean Ruff lint and formatting checks.

### Complete Community synthesis with oMLX — 2026-08-09

- A live Community answer ended with an unfinished phrase. Phoenix showed a
  normal `stop` after 287 output tokens, below the existing 512-token ceiling,
  proving that the ceiling was not responsible.
- Tightening the prose prompt while retaining strict JSON-schema generation did
  not help; the next live answer stopped at the same kind of unfinished phrase.
  The same evidence produced complete prose when strict schema generation was
  removed, isolating the problem to that structured-output path with the
  configured Qwen/oMLX combination.
- Tested LangChain's official `json_mode`. Its first output contained complete
  prose but represented `answer` as an array, which Pydantic correctly rejected.
  An explicit instruction that `answer` must be one JSON string produced a
  complete four-bullet answer that passed the existing Pydantic schema.
- Community Research now uses official JSON mode with the clarified prompt. It
  retains the Pydantic output validation, 512-token ceiling, citation checks,
  and fixed graph path. No custom parser, retry, graph node, model call, or
  dependency was added.
- The implemented live workflow returned four complete bullet points and five
  validated citations. Its report is
  `artifacts/evaluations/community-research-20260809T232215Z.json`.

### Planned Local Knowledge retrieval — 2026-08-09

- Reproduced the noisy recall against the retained archive. SQLite FTS joined
  every natural-language word with `OR`, so generic words matched an unrelated
  Pistons chat. BM25 also ranked short older reports above longer newer reports,
  and a prior recall answer outranked its original evidence because every
  successful CLI response had been re-indexed.
- Added one constrained Local Knowledge planning call with exactly three
  outputs: concise search terms, `chat`/`scheduled_run`/either source selection,
  and `relevance`/`newest` ordering. SQLite remains the search engine; no
  embedding model, semantic database, reranker, dependency, or custom model
  client was added.
- Newest plans retrieve one document. Relevance plans retain the existing limit
  of five. Local Knowledge answers are no longer indexed as new chat knowledge,
  preventing recursive copies while the original evidence remains searchable.
- Clarified that bracketed citations inside an archived report belong to that
  report's external sources. Local Knowledge cites the archive document number
  instead.
- A live run against a temporary snapshot planned `weekday AI news report`,
  selected only scheduled reports, ordered newest, retrieved the August 9
  report, and answered all three developments using archive source `[1]`. The
  real knowledge database was not modified.
- Full deterministic verification completed with 129 passing tests and 16
  skipped opt-in live tests. Ruff lint and formatting checks passed for all 53
  Python files.

### Concise direct-chat default — 2026-08-09

- The acceptance run showed that a simple router-versus-specialist explanation
  expanded into multiple sections, code examples, a comparison table, and an
  analogy even though the user had not requested that depth.
- Updated only the version-controlled direct-chat system prompt. It now leads
  with the answer, uses short prose or a short list by default, avoids optional
  formatting and background, and expands when the user explicitly requests a
  detailed or step-by-step response.
- Added no graph change, output truncation, model parameter, retry, dependency,
  or deterministic assertion about model wording.
- Two ordinary live questions returned 205 and 134 words. An explicitly
  detailed request returned a 293-word step-by-step explanation, confirming
  that the concise default does not prevent requested depth.

### Final core acceptance pass — 2026-08-09

- Ran the real CLI with temporary checkpoint storage and a snapshot of the
  knowledge database. The normal ORIS conversation and knowledge
  databases were not modified.
- Direct chat returned the accepted concise router-versus-specialist
  explanation. Web Research answered the August 8 AI-agent news request with
  four dated sources.
- Ordinary natural-language Community Research reduced the request to the
  `LangGraph` topic, collected ten X results and no Hacker News results, and
  returned a complete four-bullet answer with four X citations.
- YouTube Catch-up found two new videos. It summarized and cited the available
  MeidasTouch transcript, reported the unavailable Pistons transcript as a
  caveat, and completed the normal Net-Razor acknowledgement step.
- Local Knowledge selected the scheduled-report source, newest ordering, and
  exactly one August 9 `weekday-ai-news` report. Its three claims consistently
  cited archive source `[1]`, and the derived recall answer was not added back
  to the temporary knowledge index.
- After the active conversation retained ZIP code `48383`, `/new` created a new
  session. A follow-up asking for weather at the same ZIP code requested the ZIP
  again, confirming that conversation context did not cross the session
  boundary automatically.
- All six core paths completed without an application error. The accepted
  deterministic baseline remains 129 passing tests, 16 skipped opt-in live
  tests, and clean Ruff lint and formatting checks.

### Hybrid MCP architecture direction — 2026-08-10

- Chose a hybrid architecture: fixed specialist graphs remain responsible for
  predictable research, citations, scheduling, persistence, and ordered side
  effects.
- Clarified that MCP standardizes transport and tool invocation, not the names,
  schemas, outputs, or guarantees needed to swap unrelated tools directly.
- Recorded replaceable ORIS capability adapters as the target boundary. No
  provider-selection configuration or speculative adapter will be added until
  a real second backend exists.
- Deferred an optional dynamic MCP exploration agent. If approved later, it
  will be interactive, read-only, explicitly allowlisted, bounded, and separate
  from scheduled or persistence-sensitive workflows.
- This was a documentation decision only. Source code, configuration,
  dependencies, tests, and runtime behavior did not change.
