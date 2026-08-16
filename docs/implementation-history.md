# ORIS implementation history

This file preserves the detailed implementation record: a summary of the
July 29, 2026 project snapshot, then dated milestone entries running from
August 8 onward. Everything here is a record of work already done. Any
reference to "next" work is historical and is not the active to-do list.

See [implementation-plan.md](implementation-plan.md) for current work and open
questions.

## The July 29, 2026 snapshot

The project reached that point in four planned steps, all since completed. The
snapshot is summarized here rather than reproduced. It was written in the future
tense, and every claim in it about what was "not yet built or approved" has been
overtaken by the dated record below.

- **Step 1 — Web Research specialist.** A compiled graph with fixed edges:
  `validate_request`, `plan_search`, `search_web`, a structured
  `synthesize_answer`, and a deterministic `validate_answer`. ORIS-owned search
  request, result, and response models sit behind a provider-independent
  `WebSearch` interface with the official `TavilySearch` integration behind
  that, and a typed cited-answer contract keeps source URLs application-owned.
- **Step 2 — Local visibility and evaluation.** Optional local Phoenix tracing
  through the official OpenInference LangChain instrumentor, with analytics
  disabled, loopback-only access, and 14-day retention. A versioned set of four
  representative Web Research questions, and a runner writing one timestamped
  JSON report of answers, sources, latency, and errors.
- **Step 3 — Interactive assistant.** An `oris` chat graph over LangGraph's
  `MessagesState` with a fixed conditional route between direct chat and Web
  Research; a command-line front end invoking the compiled graph directly with
  no separate server; durable history through the official SQLite checkpointer;
  and a separate SQLite FTS5 archive queried explicitly through `/recall`.
- **Step 4 — Scheduled operation.** Deferred at the time of the snapshot. Its
  accepted design — a project-root `schedules.toml` as the source of truth, one
  APScheduler process recreating schedules at startup, a transitional per-user
  LaunchAgent, and timestamped Markdown artifacts as the deliverable — is
  recorded in [ADR 002](architecture/002-project-owned-scheduling.md) and was
  implemented across the August 8 milestones below.

Three things the snapshot ruled out have since been approved and built:
automatic routing among specialists, MCP integration through Net-Razor, and a
bounded defensive Threat Intel specialist behind an explicit command. The
reasoning for each is in the dated entries and the architecture records.
Remote-write tools, autonomous side effects, and n8n remain excluded.

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

### Terminal interface wired to real data — 2026-08-12

- Replaced the placeholder terminal interface with a working one over the same
  compiled graph, the same command table, and the same knowledge archive as the
  command line. The graph call runs in a Textual worker, so the long part of a
  turn does not block the interface and a raised error costs the turn rather
  than the session. Everything around it — session listing, transcript replay,
  trace reads, evidence listing, the archive write — runs on the event loop,
  measured later at about 4 ms per turn and recorded as a known cost.
- Moved the command vocabulary to `src/oris/commands.py`. Both front ends now
  parse one table, so neither can support a command the other does not.
- Added `src/oris/observability.py`: read-only queries against Phoenix's own
  SQLite file. No Phoenix client, no running server. It recovers each run's
  request, mode, and thread from the OpenInference attributes on the root span,
  which is what turns a list of timings into a list of turns.
- Added session listing and transcript replay over `checkpoints.sqlite`. A
  session is named by its most recent request: naming it by the first one meant
  a long-running session carried a name from days earlier next to a timestamp
  from minutes earlier, which read as unrelated data.
  Thread IDs come from SQL because `SqliteSaver.list()` requires a thread_id;
  every per-thread read goes through the checkpointer. Choosing a session writes
  `data/current_session`, so both front ends resume the same conversation.
- Prompt viewing shows the system prompt each model call was actually given,
  read from the trace rather than from `src/oris/prompts/`. Prompt editing was
  considered and rejected: it would make the interface a second, unversioned
  source of prompts.
- Evidence is reachable from one tab only. `e` and the other run-scoped keys
  are disabled and hidden outside Activity through Textual's `check_action`,
  and `/threat show` from the chat switches tabs rather than opening a second
  entry point to the same viewer.
- Stored evidence is matched to the run that produced it by timestamp
  containment. Nothing records the link, and inventing an index was not worth
  it for a correlation the filenames already support.
- Session deletion removes the conversation through the checkpointer's own
  `delete_thread` and its archived answers through a new
  `KnowledgeRepository.delete_by_source_ref`, behind a confirmation that states
  the count of both. Phoenix traces are left alone. Deleting the active session
  starts a fresh one rather than leaving the interface pointing at a gap.
- Every external *read* degrades to an explanation rather than an error: no
  Phoenix database, an unreadable schema, no checkpoints, and no reports are
  all ordinary states. Rendering what a successful read returned did not
  degrade, which a later review found and a later entry records.
- Deterministic baseline after this work: 212 passing tests, 17 skipped opt-in
  live tests, clean Ruff lint and formatting.

### Foundation review fixes — 2026-08-13

A max-effort review of the foundation produced thirteen findings. Everything
below was fixed against the running system, one change at a time, with the
review treated as evidence rather than as instruction: three of its claims did
not survive contact with the code and are recorded here as corrections.

- **The terminal interface could be closed by a request that mentioned a
  path.** `DataTable` runs string cells through rich's markup parser, so a
  request containing `[/var/log/syslog]` raised `MarkupError` in the table's
  idle handler and took the application down. Because the activity table is
  rebuilt from the trace store on every start, the interface stayed shut until
  the trace aged out. Runtime text is now added as `Text`, which is the
  discipline the command line already documented.
- **`/threat report` discarded findings when a request named more than one
  indicator.** The pivot was keyed by field and then by source, so provider
  names repeated across subjects and one indicator's answer silently replaced
  another's — an abuseipdb confidence of 0 and of 100 collapsed to whichever
  came last. It is now keyed by subject, then field, then source, and failed
  providers keep a per-subject code. Measured against a real one-indicator,
  ten-provider report the pivot is 84% of the raw fan-out, so what justifies
  putting it in the conversation is the legibility of the reorganisation, not
  a size saving. ADR 001's claim of "about a fifth" was wrong and is corrected.
- **Nothing but the model had a timeout.** MCP sessions now carry a read
  timeout — without one the SDK skips its own guard entirely and a request
  waits forever, which is an endless spinner interactively and a held job slot
  in the scheduler. Node ceilings were added only where a node makes many calls
  it cannot individually bound: evidence collection and video summarisation.
- **The router's prompt did not say to treat earlier messages as untrusted.**
  One line, no schema change.
- **Web search was unbounded at every layer, and the review's recommended fix
  was illegal.** LangGraph refuses a node timeout on a synchronous node,
  because Python cannot cancel a blocking call in place; `search_web` was
  synchronous. Verified in the installed library: `langchain-tavily` posts
  through `requests` with no timeout argument, and through aiohttp with a
  300-second overall and 30-second connect ceiling. The tool exposes no timeout
  setting, so the search capability is now asynchronous end to end, which is
  how the call acquires a deadline at all. The scheduler reaches it through the
  async API for the same reason.
- **Storage moved to a fixed `~/.oris`.** Relative defaults resolved against
  whatever directory a process started in, so the interactive session, the
  scheduler under its LaunchAgent, and a shell one level down each built a
  private conversation history and knowledge index while reporting nothing;
  `/recall` simply stopped finding yesterday's answers. Configuration moved
  with it. A leading `~` in an override is now expanded, because otherwise the
  natural way to write one creates a directory named `~`.
- **Deleting a conversation left its threat evidence behind** for up to a
  month — the most sensitive files ORIS writes. Reports now name their
  conversation in the filename and the header, so deletion finds them without
  an index that could fall out of step, and the confirmation counts them. The
  filename splits on `-`, so the thread is last and is the only field allowed
  to contain one.
- **Evidence failed to attach to its own run on a sub-second boundary**, and
  could attach to a run in another conversation. Filenames keep whole seconds,
  so a report written a fraction of a second into a run dates from just before
  it started; the comparison now allows exactly that much, and the conversation
  narrows the candidates before the time picks the turn.
- **Local Knowledge had no citation check and no bound on its evidence.** It
  now rejects a citation pointing at a document that was never retrieved, but
  still allows an answer that cites nothing, because this specialist is told to
  say when the archive cannot answer and that response is honest precisely
  because it has no citation. Each document is truncated to 3,000 characters
  with the cut announced.
- **`/threat show *` opened a report** instead of reporting an unknown ID: the
  ID reached `Path.glob`, where `*`, `?`, and `[…]` are patterns. It is checked
  against the shape it is generated in first. Not a traversal; the review
  probed that and found none.
- **Importing the composition root created a database.** The knowledge
  repository built its schema in its constructor, so importing that module — to
  read a setting, to collect a test, to list graphs for the development server
  — wrote a directory and a file wherever the process resolved the path. The
  schema is now created on first use.
- **The live LangSmith credential was removed** from the environment file, and
  `langgraph.json` no longer declares an `env` file at all: ORIS reads its own
  configuration from a fixed path, so no separate process needs to be handed
  the whole file. Nothing had ever read the key — settings ignore unknown
  variables and add nothing to the environment — but the development server
  would have.
- **The two front ends stopped duplicating each other.** The command reference,
  the reading of a typed line, and the archive-on-success rule now live in one
  place each. The terminal interface accepted `/quit` and the command line did
  not, which is the drift a shared vocabulary exists to prevent.

Corrections to the review, which was written without running the code:

- Its central timeout recommendation was impossible as written, as above.
- `dist/` was described as empty; it held a stale wheel and sdist, now removed.
- `create_youtube_catch_up_graph` was listed as dead. It has no production
  caller but eight test callers, and deleting it would add code rather than
  remove it, so it stays. `Trace.span_count` and
  `WebSearchResponse.provider_response_time_seconds` were genuinely unread and
  are gone; `provider_request_id` stays because a live contract test reads it
  and it is the handle to quote to the provider; `relevance_score` stays
  because the planned web-evidence MCP boundary specifies it.
- The archive-on-success block was described as duplicated four times. Two of
  those build a scheduled-run document keyed by report path, which is a
  different fact, not the same one written twice.

Deterministic baseline after this work: 244 passing tests, 17 skipped opt-in
live tests, clean Ruff lint and formatting. Storage was measured while doing
it: everything ORIS owns is 2.4 MB, against Phoenix's own 36 MB, which settles
the plan's storage-retention deferral without building anything.

### Certificate trust and per-step status — 2026-08-14

- **Web Research broke, and moving the search to the asynchronous path is why.**
  `requests` bundles certifi and always has root certificates; aiohttp uses
  Python's default SSL context, which on this python.org macOS build reads a
  `cert.pem` that only exists once `Install Certificates.command` has been run.
  Measured: that context loaded **zero** certificates, so every Tavily request
  failed the TLS handshake while the synchronous path had worked for weeks.
  Building the client now points OpenSSL at certifi when the interpreter has no
  roots of its own, leaving a deliberate `SSL_CERT_FILE` alone. Verified with a
  real handshake to `api.tavily.com`. `certifi` becomes an explicit dependency
  because the code imports it directly; it was already installed through
  `requests`.
- **A `/threat` run looked silent.** It was not — the interface has always shown
  a label and a timer — but the label was one dim line at the bottom edge that
  never changed. The traces say why that matters: a real run took 29 seconds,
  **23 of them inside the final model call**. One unchanging label across that
  wait cannot distinguish working from hung.
- Turns are now streamed rather than invoked, and each graph node is named as it
  **starts**. Node updates arrive on completion, which is exactly too late to
  say what is running, so this reads the debug stream instead. `subgraphs=True`
  is what makes it useful: the specialists' own nodes are where the time goes.
  Both front ends share the runner and the wording; the terminal interface also
  gained a spinner frame, because a number that only changes once a second
  reads as static.

Deterministic baseline after this work: 247 passing tests, 17 skipped opt-in
live tests, clean Ruff lint and formatting.

### Evaluation runner repair and documentation cleanup — 2026-08-14

- **The asynchronous search move broke a second thing, found while reviewing
  documentation rather than by any check.** `evaluation.py` still called
  `graph.invoke` on the Web Research graph, which had gained an asynchronous
  search node. LangGraph refuses that combination — verified against the
  installed package, which raises `TypeError: No synchronous function provided
  to "search_web"` — so every evaluation case would have been recorded as a
  failure. The runner and its entry point are now asynchronous.
- The reason nothing caught it is the same reason nothing caught the
  certificate failure the day before: **the double replaced the thing that
  broke.** The runner's test drove a `Mock()` graph, which accepts whatever call
  it is given and therefore agrees with the runner about the calling convention
  no matter what either one does. The test now compiles the real Web Research
  graph with the existing search and model fakes. Reverting the fix makes it
  fail, with both cases erroring and the search never reached — which is exactly
  what a real evaluation run would have produced.
- Test rationale, per the project rules. *Invariant:* the evaluation runner can
  drive the real graph and read the state keys it depends on, and one failing
  case neither hides a passing case nor prevents the report. *Deterministic:*
  yes — graph wiring, calling convention, and report shape involve no model
  judgement; the fake model returns fixed structured output. *Generalises:* it
  asserts nothing about answer wording, only that a case completes against the
  real graph and that its status, latency, and source count are recorded, so any
  future change to the call convention or the output keys breaks it.
  *Why pytest rather than the evaluation set:* the evaluation set measures
  answer quality against live services; this measures whether the runner can run
  at all, which is a deterministic contract and should block.
- **The missed scheduled run is explained and closed.** The `weekday-ai-news`
  job did not fire on the morning of 2026-08-14 because the MacBook was asleep.
  APScheduler skips executions missed while it was not running, and a sleeping
  host is that same case. Recorded as a consequence in ADR 002 and in the README
  rather than investigated further; it is a property of scheduling on a laptop
  and is one of the reasons for the Mac mini.
- **Documentation cleanup.** The history file carried 380 lines of the July 29
  plan snapshot, written in the future tense, including claims that were no
  longer true — that scheduled runs did not populate the knowledge index, and
  that security-research specialists were outside the plan. It is now a
  thirty-line summary of the four planned steps, and the file is 907 lines
  instead of 1,253. The active plan's "Things to consider later" mixed open
  questions with decisions already settled against a measurement; those are now
  two separate sections, so a settled decision is visibly settled. Its claim
  that everything ORIS stores lives under `~/.oris` was overstated: scheduled
  reports and run history still resolve relatively, pinned to the checkout only
  by the LaunchAgent's working directory. That is now recorded accurately and
  listed as an open question rather than quietly fixed, because moving them
  moves existing files.
- Roadmap additions: schedule management in the terminal interface, a review of
  the eleven system prompts together with the evaluation coverage needed to
  judge a prompt change, and a re-test of scheduled execution on the Mac mini.
  SearXNG and Firecrawl were already on the roadmap inside the Web Evidence MCP
  entry; that entry now says plainly what each one buys instead of naming them
  in passing.
- ADR 001 gained the certificate-trust decision, which is a portability
  decision: the Mac mini will have the same interpreter build, and the
  application must not depend on someone having run its certificate installer.
  ADR 002 records the measured storage outcome that settled its own deferred
  retention question.

Deterministic baseline after this work: 247 passing tests, 17 skipped opt-in
live tests, clean Ruff lint and formatting. Unchanged, because the runner's
existing test was rewritten rather than joined by a second one — the failure it
now catches is the failure it was always meant to catch.

### Certificate fix repaired, and the live contracts actually run — 2026-08-14

- **The certificate fix did not work.** It was correct about what to do and
  wrong about when. aiohttp builds its verified SSL context once, at *its own*
  import, and caches it in a module global — the source says so in a comment.
  The repair ran later, when the Tavily client was constructed, by which point
  importing `langchain_tavily` had already frozen a context holding **zero**
  certificates. Measured directly: the cached context had 0 CAs while a freshly
  built one had 121.
- It now runs in the package's `__init__`, which is the one place guaranteed to
  execute before any `oris` module body and therefore before aiohttp is
  imported. Confirmed first by setting the variable in the shell, which made the
  live Tavily contract pass, and then by the relocated code passing the same
  contract with a clean environment.
- **The test that was supposed to prove this asserted the wrong thing.** It
  built a fresh default context and checked that one had roots. That is true and
  irrelevant, because no fresh context is ever what aiohttp uses. It now runs a
  clean subprocess and asserts on the context aiohttp actually holds, because
  import order is the entire contract and cannot be observed inside a process
  where everything is already imported.
- Test rationale, per the project rules. *Invariant:* by the time aiohttp is
  imported inside an ORIS process, OpenSSL has a root store, so an HTTPS call
  can complete its handshake. *Deterministic:* yes — an import-ordering and
  configuration fact, with no model and no network. *Generalises:* it asserts
  that trust exists, not which bundle or how many roots, so it holds equally for
  an interpreter that already has its own, one falling back to certifi, and one
  pointed at a corporate bundle. *Why pytest rather than the evaluation set:* it
  is a startup contract whose failure silently removes every web capability.
  Its one honest weakness is that on a correctly installed interpreter it would
  pass regardless — it only detects the regression on a machine that has the
  underlying problem, which is the machine that matters.
- **Two more live contracts were broken by the same asynchronous move**, both
  invisible because they are opt-in and were never run: the Tavily contract
  called the search without awaiting it, and the Web Research contract called
  the graph synchronously. That is the fourth and fifth instance of one class —
  a caller that no longer matches the code it calls, hidden behind either a
  double or a disabled switch.
- **Fifteen of the seventeen live contracts were then run against the real
  services and pass:** five oMLX model contracts, Tavily search, two search
  planning calls, Web Research end to end, routing across all seven evaluation
  cases, Local Knowledge, Community Research and its command-line variant, the
  Net-Razor stdio contract, and Threat Intel's local reference path. Community
  Research failed once with a connection error during synthesis and passed on
  retry in 15 seconds, so that is recorded as a transient oMLX blip under
  concurrent load rather than a defect.
- The two YouTube contracts were deliberately not run. Both acknowledge
  processed videos back to Net-Razor, which is a real state change on the user's
  own data — a later catch-up would silently skip whatever they marked. They
  need an explicit decision, not a blanket "run everything".

Deterministic baseline: unchanged at 247 passing, 17 skipped, clean lint and
formatting. Live baseline: 15 of 17 contracts passing against real services.

### The status was never invisible to the code, only to the reader — 2026-08-14

- **The per-step status was correct all along and could not be seen.** The
  status line and the prompt were both docked to the bottom edge of the same
  container. The prompt is three rows tall and the status one, and their regions
  overlapped on the last of those rows; being later in document order, the
  prompt was composited on top. Measured in a real headless run: the widget's
  content read `⠋ Threat Intel · writing the answer … 2.0s` while the painted
  screen contained neither "Threat Intel" nor "writing the answer". Removing the
  dock puts it in normal flow directly above the prompt, where an empty one is
  zero rows high and gives the line back between turns.
- **Every existing assertion about it passed, in both states.** The step test
  asks the widget to render its own line, which a covered widget still does
  perfectly. Confirmed by reverting the layout: the step test stayed green and
  only the new one failed. That is the same shape as the four failures before
  it — a check that consults the component rather than the result.
- The new test reads the composited screen. *Invariant:* while a turn runs, the
  status is painted where the reader can see it, not merely set on a widget.
  *Deterministic:* yes — layout geometry and compositing, driven by a fake graph
  with no model involved. *Generalises:* it asserts the label appears somewhere
  in the rendered screen, not where or in what colour, so any future layout
  change that hides it fails regardless of the cause. *Why pytest rather than
  the evaluation set:* a deterministic interface contract whose failure silently
  removes all progress feedback.
- Two diagnostic attempts were wrong before this one landed, and both were
  caught before being reported. The first read a `renderable` attribute that
  Textual 8.2.8's `Static` does not have, so it reported an empty status for a
  working interface. The second matched plain text against a screenshot that
  encodes spaces as entities and splits lines across style spans, so it reported
  a hidden status for a visible one. Reading the installed source settled both.
- Recorded because it generalises: three of this session's bugs and two of its
  failed diagnoses share one cause. A check that asks the component under test
  what it did will agree with it. Only a check that reads the result — the
  painted screen, the real graph, the cached SSL context — can disagree.

Deterministic baseline after this work: 248 passing tests, 17 skipped opt-in
live tests, clean Ruff lint and formatting.
