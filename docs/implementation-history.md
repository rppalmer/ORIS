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

### Verifying the review fixes against reality — 2026-08-16

The fixes above were verified against fakes. Running them against real services
found five defects, all of one kind, and every one of them had a green test
sitting on top of it.

- **Moving the search to the asynchronous path broke every search.** `requests`
  bundles certifi and always has root certificates; aiohttp uses Python's
  default SSL context, which on a python.org macOS build reads a `cert.pem`
  that exists only once `Install Certificates.command` has been run. Measured:
  that context held **zero** certificates against certifi's 121.
- **The first repair was correct and useless.** aiohttp builds its verified
  context once, at *its own* import, and caches it in a module global — its
  source says so in a comment. Repairing the environment when the Tavily client
  was constructed happened long after `langchain_tavily` had already frozen an
  empty context. The repair now runs in the package's `__init__`, the one place
  guaranteed to execute before any `oris` module body. Verified by a real
  handshake to `api.tavily.com`.
- **The evaluation runner had been broken by the same change.** It still called
  the graph synchronously, which LangGraph refuses once a graph holds an
  asynchronous node, so every case would have been recorded as a failure the
  next time a prompt change needed measuring.
- **Two live contracts had been broken by it too** — the Tavily one called the
  search without awaiting it, the Web Research one called the graph
  synchronously. Neither had been run, so neither had said so.
- **A `/threat` run looked silent.** It was not: the interface showed a label
  and a timer, but the label never changed. The traces say why that matters — a
  real run took 29 seconds, **23 of them inside the final model call**. Turns
  are now streamed and each graph node is named as it *starts*, which is why
  this reads the debug stream rather than node updates: an update arrives on
  completion, exactly too late to say what is running. `subgraphs=True` is what
  makes it useful, because the specialists' own nodes are where the time goes.
- **That status was then invisible anyway.** It and the prompt were docked to
  the same edge; the prompt is three rows tall against the status line's one,
  their regions overlapped, and the prompt was composited over the top. The
  widget's content read correctly throughout while the painted screen contained
  none of it.

The common cause, worth stating once: **a check that asks the component under
test what it did will agree with it.** A mock graph agreed with the runner about
a calling convention that had become illegal. A certificate test built a fresh
SSL context rather than reading the one aiohttp had cached. A status test asked
the widget to render its own line, which a covered widget still does perfectly.
Two diagnostic probes failed the same way and were caught before being reported.
Tests now read the result — the painted screen, the real compiled graph, the
cached context — and each was proved by reverting its fix and watching it fail.

Test rationale, per the project rules, for the three tests added. *Invariants:*
the evaluation runner can drive the real graph and read the state keys it
depends on; aiohttp has a root store by the time it is imported; the status is
painted where the reader can see it. *All deterministic* — graph wiring, import
ordering, and layout geometry, with no model involved. *All generalise* — they
assert that trust exists rather than which bundle, that a case completes rather
than what it says, that the label appears rather than where. *All belong in
pytest* rather than the evaluation set, being startup and interface contracts
whose failure is silent and total. The certificate test's honest weakness: on a
correctly installed interpreter it passes regardless, so it detects the
regression only on a machine that has the underlying problem.

**Fifteen of the seventeen live contracts were then run against real services
and pass**, and the two YouTube contracts followed once their side effects were
approved: they acknowledge processed videos back to Net-Razor, which is a real
change to the user's data. **All seventeen now pass.** Community Research failed
once with a connection error during synthesis and passed on retry in 15 seconds,
recorded as a transient oMLX blip under concurrent load rather than a defect.

Documentation was brought back in line at the same time. The history file
carried 380 lines of the July 29 plan snapshot, written in the future tense,
including claims that had gone false. The active plan mixed open questions with
decisions already settled against a measurement; those became two sections. Its
claim that everything ORIS stores lives under `~/.oris` was overstated —
scheduled reports and run history still resolve relatively — and that is now
recorded accurately as an open question rather than quietly changed.

Deterministic baseline: 247 passing, 17 skipped, clean lint and formatting.

### Phoenix as a supervised service — 2026-08-16

- **Phoenix had been down for two days and nothing said so.** The only signal
  was an empty activity pane, and the only way to start it was remembering a
  shell script in another terminal. Nothing had been traced since 2026-08-14
  18:38.
- **ORIS had two ways of launching its own services.** The scheduler ran from an
  absolute path inside the project's virtual environment under launchd; Phoenix
  ran from a shell script that called `uvx` off PATH and restated the trace
  directory in its own words, with a comment conceding the two had to be kept in
  step by hand. Both now render the same way: one label built from the service
  name, one absolute executable in `.venv/bin`, one pair of logs named after it.
  A test asserts that rule across every service rather than leaving it to be
  remembered.
- **The collector is launched through its own console script, not `uvx`.** `uvx`
  runs the collector as a *child* rather than replacing itself, so launchd
  supervised a wrapper: measured, a stopped service left the previous collector
  alive holding port 6006, and its replacement then failed to bind the gRPC port
  and crash-looped under `KeepAlive`. Verified after the change that the launchd
  job ID and the process holding the port are the same, that a restart replaces
  it leaving no orphan, and that the scheduler is untouched throughout.
- The collector's environment is derived from ORIS's own settings, so the
  directory ORIS reads and the one Phoenix writes cannot drift.
- **The activity pane could not distinguish three different empty states.** It
  now separates a store that has never recorded anything from one holding other
  sessions' runs, naming the newest entry and its age; it says how many runs the
  session scope is hiding; and it counts conversation turns beside traced runs.
  That last one matters because the two are different sets — a failed request is
  removed from the conversation but keeps its trace, and a turn taken while the
  collector was down stays in the conversation with no trace. One real session
  had one of each and shared nothing between the two panes.
- A failed ThreatSyft call is attributed to the request rather than borrowing
  the subject's name, which had produced entries reading
  `{"not-an-ip": {"not-an-ip": "invalid_indicator"}}`.

Deterministic baseline: 257 passing, 17 skipped, clean lint and formatting.
All 17 live contracts passing against real services.

### Telling every synthesis prompt what day it is — 2026-08-17

- **Only the search planner knew the date.** Web Research, Threat Intel, Local
  Knowledge, Community Research, and direct chat all synthesized without it, so
  a specialist asked to judge whether evidence is current had nothing to judge
  it against, and direct chat answered as of its training cutoff with no way to
  know it was doing so. Observed before the change: a Web Research answer placed
  a page of unknown vintage beside genuine current reporting and ranked them
  the same.
- The date is appended per call rather than folded into the packaged prompt at
  import, because the scheduler and a terminal session are long-running
  processes that outlive the day they started on.
- The planner keeps supplying the date in its human turn, where its prompt is
  written to expect it, and now takes the wording from the same helper so the
  line has one definition. Its wire format is unchanged, which its existing test
  asserts byte for byte.
- YouTube Catch-up was deliberately left out: both its prompts already receive
  each video's `published_at`, which is the only date judgement they make.
- No new tests. The invariant — the composed system message carries today's date
  — was added to the message-inspection assertion each specialist's existing
  test already makes. It is deterministic because ORIS builds that message
  itself, and it names no example date, so it holds on any day the suite runs.
  Whether knowing the date *improves* an answer is semantic and belongs to the
  evaluation set instead.

Deterministic baseline: 257 passing, 17 skipped, clean lint and formatting.

### Reading a paged MCP result to its end — 2026-08-17

- **YouTube Catch-up read the first page of a transcript and called it the
  video.** Net-Razor serves a long transcript in parts of about 40 KB and
  reports `part`, `part_count`, `offset`, `next_offset`, and `truncated`;
  ORIS read one response, noted "transcript truncated" as a caveat, summarized
  that part, and then acknowledged the video back to Net-Razor. Acknowledgement
  removes it from the queue, so the rest of a long video was lost permanently.
  Nothing failed and nothing looked wrong — the digest was simply built from the
  opening minutes.
- **`truncated` never meant what it was being read to mean.** On that tool it
  says only that the transcript spans more than one part, and it is set on the
  final part too. `next_offset` is the field that says whether anything is still
  unread. The video is now reported truncated when parts remained and ORIS
  stopped, not when the provider split the response.
- **Each part is summarized on its own rather than concatenated.** Keeping one
  part in context is the reason Net-Razor pages at all; joining the parts and
  summarizing once would rebuild exactly the oversized input the paging avoids.
  Parts after the first are served from Net-Razor's local storage, so following
  the chain costs nothing upstream.
- **Three parts per video is an ORIS budget, not a provider limit.** Net-Razor
  cannot know how many model calls one catch-up run can afford, and the run has
  a fixed 900-second timeout. Three parts covers roughly two and a quarter hours
  of speech. A video past that is summarized from what was read and reported
  truncated. The worst case triples the summary calls a run can make, and its
  interaction with the run timeout is unmeasured against real long videos.
- Net-Razor's other two tools in ORIS's allowlist do not page: `net_razor_research`
  returns every source's results in one response bounded by
  `max_results_per_source`, and `net_razor_yt_new_videos` returns a compact
  queue. Community Research needed no change.
- The two new tests protect the offset chain being followed to its end and the
  part budget stopping honestly rather than silently. Both are deterministic
  tool-call contracts against a scripted double. The existing test had driven
  truncation with a single-part response carrying `truncated: true`, a shape
  Net-Razor never emits; its double now speaks the real contract.

Deterministic baseline: 259 passing, 17 skipped, clean lint and formatting.

### One evaluation runner for four specialists — 2026-08-17

- **The runner was Web Research's, not a runner.** It hardcoded one case file,
  one input key, and one result shape, so the four specialists without a
  versioned set could not get one without a second copy of it. It now holds one
  small table of how each specialist is asked and where its answer and citations
  live — `query` against `topic` against `request`, `sources` against
  `cited_urls` against `sources_used` — and a set names the specialist it
  belongs to.
- Versioned case files added for Local Knowledge, Community Research, and
  Threat Intel. The reports they produce record what was asked, what came back,
  what it cited and how long it took; there is no scoring and no model grading
  another model's prose. Judgement is a person reading two reports side by side.
- **`version` is no longer pinned to a single number.** Each file carries its
  own, and its job is to tell a reader whether two reports asked the same
  questions. Pinning it made sense with one file and is wrong with five.
- The report's `sources`/`source_count` became `citations`/`citation_count`,
  because what a specialist cites is a web result, an archive document, a URL,
  or a provider name depending on which one it is. Reports written before this
  use the older key names.
- One test drives every specialist in the table through its real compiled graph
  with scripted dependencies. It exists because a wrong key is invisible until a
  live run fails partway through and produces no report — and because a graph
  double would have agreed with whatever call the runner made, which is how the
  runner previously came to be calling a synchronous `invoke` on an
  asynchronous graph. Running it found the calling convention correct for all
  four.
- **This does not close the evaluation-coverage item and is recorded in the plan
  as unfinished.** Local Knowledge and Threat Intel cases run against live state,
  so two reports differ for reasons unrelated to the prompt; judgement is
  entirely manual with no recorded per-case verdict; YouTube Catch-up takes no
  question and does not fit this shape at all; and the cases are a first draft
  written against the prompts' stated rules rather than against observed
  failures.

Deterministic baseline: 260 passing, 17 skipped, clean lint and formatting.

### What the first evaluation run found — 2026-08-18

- **Running the new evaluation set for real found three defects, none of them
  in the specialist the run was aimed at.** The set was written the day before
  against the prompts' stated rules. Its first live run was the first time
  anything checked those rules against real archives and real feeds.
- **The knowledge archive matched exact word forms.** Six documents said
  "scheduled" and a question about "schedules" reached none of them. On its own
  that would have returned nothing, which is a visible failure. But search ORs
  its terms together, so the miss on the important word was filled by unrelated
  documents sharing a common one, and a question about how ORIS schedules jobs
  came back with dynamic-DNS threat enrichment. A confident wrong answer, not an
  empty one. Switching FTS5 to the porter tokenizer fixed it: on the real
  archive that question now returns the five scheduled reports.
- **Changing the tokenizer was not enough on its own.** The table is created
  with `IF NOT EXISTS`, so every archive already on disk would have kept the old
  tokenizer while the code claimed otherwise — the worst kind of fix, one that
  works on a fresh machine and nowhere else. Opening an archive now re-indexes
  it once if it was built the old way.
- **Recency-ordered retrieval asked for exactly one document.** That existed to
  keep a recurring-report question answered from the newest run rather than
  blended across several. The system prompt already says exactly that, and
  enforcing it at retrieval applied it to every question the planner read as
  recency-sensitive. Both orders now retrieve five; recency still decides which
  document is source 1. Verified against the real archive: the recurring-report
  case sees five reports and still answers from the newest alone, so the prompt
  was carrying that rule by itself.
- **The Community Research cases measured nothing.** Every one returned zero
  evidence, which looked like a specialist that could not find anything. The
  cases asked full English questions, and the whole sentence was going to
  Algolia and X as the search term. This specialist never sees a question in
  production — the router strips the request to a bare topic first — so the file
  was testing a path that does not exist. Rewritten as topics, the cases take 25
  seconds instead of 3 and return real posts.
- **I got the Local Knowledge diagnosis wrong twice before querying the
  archive.** First "inert cases", then "a real retrieval defect", then back to
  inert once I actually ran the queries. The two defects found on the way were
  real and are fixed, but neither changed the verdict on those cases. The
  evaluation set is worth running; my reading of it without evidence was not.
- **Whisper transcription for caption-less YouTube videos was planned and then
  killed.** The plan is kept and marked dead rather than parked. Net-Razor's
  entire audit history held four caption failures, so there was nothing to build
  on either side; shortly afterwards YouTube audio stopped being downloadable at
  all, which settled it.

No deterministic baseline is recorded for this entry. It was written on
2026-08-25 from the commits, and the count at the time cannot be reconstructed
reliably: a checkout without the `tui` extra silently collects 26 fewer tests
rather than failing, so any number measured after the fact would be wrong in an
invisible way.

### Podcast Catch-up — 2026-08-19 to 2026-08-25

Net-Razor gained a podcast source with local Whisper transcription. This is the
ORIS side of it: a fifth specialist that discovers new episodes from configured
feeds, gets a transcript for each, summarizes them one at a time, and produces a
cited digest.

**Why it is not built on YouTube Catch-up**

- **It is a candidate replacement for YouTube Catch-up, not a sibling.**
  Collecting from Google keeps getting harder; podcasts with Whisper are a bet
  on a better path to the same thing. That makes the duplication between the two
  correct and permanent rather than a merge deferred to later, because sharing
  code with something scheduled for deletion turns that deletion into an
  untangling. Net-Razor reached the same conclusion independently on its side.
- **The boundary is stated as a test anyone can run.** Deleting the YouTube
  specialist, its prompts, its tool allowlist, its builders, its job type and
  its table rows must leave every podcast test passing. One edit needed inside a
  podcast file means the separation failed. Podcasts have their own prompt files
  for this reason — pointing at YouTube's would have been the quietest possible
  entanglement.
- **One earlier decision was reversed to hold the line.** Both catch-ups were
  briefly given slash commands, `/podcasts` and `/videos`. `/videos` was dropped:
  it added a command for a specialist that may be deleted, and changed YouTube's
  reachability for reasons that were entirely about podcasts.

**The transcript-ordering rule**

- **A published transcript is never replaced by a machine one.** Net-Razor's
  store is first-writer-wins, so transcribing an episode whose publisher
  transcript was never fetched forecloses that better version permanently. The
  published one usually identifies who is speaking and machine transcription
  never does, so this is not an optimisation.
- **Transcription is reachable from exactly one branch**: the first transcript
  page came back with `no_transcript_found`. The decision is made on the first
  page only — a later page failing means a transcript already exists, and
  falling back there would trade it away to recover one page.
- **Every episode reports which backend produced its transcript.** A reader who
  cannot tell will weigh a mangled product name exactly as heavily as one the
  publisher wrote down.

**Timeouts**

- **Transcription gets its own MCP client.** The session timeout is fixed when
  the client is built and the official adapter never passes a per-call override,
  so a separate deadline means a separate client. Sharing one would have given a
  hanging feed fetch 23 minutes to fail in instead of two.
- **1380 seconds is derived, not chosen.** Net-Razor bounds a transcription in
  three stages that run in sequence — a 30-second feed fetch, a 300-second audio
  download, and a 900-second transcriber — so 1230 seconds is the longest a call
  can legitimately take. ORIS waits longer so its deadline never wins the race:
  Net-Razor classifies its own failures, and a transport timeout firing first
  would replace that description with a dead session.
- **That ceiling was not real until Net-Razor fixed it.** The download had no
  total bound, only a gap-between-chunks timeout, so a slow trickle could have
  streamed a large episode for hours. This was one of four concerns raised
  against Net-Razor before building; two of them turned out to be bugs.

**Four things only live feeds found**

- **The run budget was spent on the newest episodes globally.** A show
  publishing daily took six of eight slots and two weekly shows never appeared.
  Net-Razor already caps what each feed contributes, and flattening everything
  into one newest-first list threw that fairness away. Raising the budget does
  not help — it only admits more of the same show. Selection now takes one
  episode from each feed before a second from any.
- **The part budget was fitted to a sample twice and was wrong twice.** Six was
  set against an 83,368-character episode and cut it short. Eight was set just
  above that same sample, and the next week's episode of the same show arrived
  at 103,684 characters and was cut short again. The axis was wrong, not the
  number: a per-episode cap punishes exactly one thing, a long episode, while
  short ones leave the budget unused. An episode is now read until Net-Razor
  stops returning a `next_offset`, and the ceiling moved to the run — sixty
  parts across all its episodes, derived from ten episodes averaging six parts
  at a measured sixteen seconds to summarize one.
- **A good digest was discarded for citing nothing.** Four episodes summarized,
  a solid cross-cutting digest written, and the run died at the last step
  producing no report at all. The prompt never told the model that each supplied
  episode carries a `url` field, so it was asked for something it was never
  shown where to find. Citing a URL that was never supplied is fabrication and
  stays fatal; citing none is a formatting miss, and the report already lists
  every episode with its canonical URL, so it is now a caveat. **This is the
  same defect already recorded against Community Research, which I then copied
  into new code.** Unit tests could not catch it because they mock the digest
  model to always return a citation.
- **`/podcasts` was rejected outright and the command never worked.** The parser
  refuses any slash command with an empty request, because every command until
  then needed a subject. Podcast Catch-up already knows what to catch up on. An
  empty line is now the ordinary way to use it, listed per command so
  `/research` still refuses one.

**Two things copied from the YouTube shape that were wrong**

Discovery returns `items`, not `episodes`. And it has no `caveats` list — a feed
it could not read arrives in `errors`, so without reading those, a run covering
six of eight feeds looked identical to one covering all eight.

**Naming one show**

`/podcasts <show>` returns that show's newest episode alone, matched on the
display name Net-Razor already returns so ORIS never learns a feed URL.
Narrowing to a configured show is not the same as supplying an arbitrary one,
so the capability boundary holds.

Transcription was initially kept out of the interactive graph entirely, on the
rule that a chat turn must never start work that blocks for minutes. That line
was in the wrong place: three of the five feeds in real use publish no
transcript, so naming a show — the only reason to name one — answered "nothing
to summarise" almost every time. What actually matters is how many
transcriptions one run can stack up. A named show is one episode and so at most
one transcription, already bounded at 23 minutes. A catch-up can queue five,
which would hold the interface for most of an hour, so chat still refuses to
transcribe for one.

**Also in this period**

- Every slash command now answers `--help` or `-h`, and `/help` takes a command
  name. `/podcasts --help` had been read as the name of a show, so the command
  most likely to be asked about searched the feeds for a podcast called
  "--help".
- The README gained a section on standing the project up on a second machine,
  since the existing setup notes assumed the machine you were already on.
- One episode, `93bfba91`, was acknowledged by mistake during testing and is
  permanently out of the queue. Acknowledgement is one-way. The cause was
  reproducing a bug with the acknowledging graph when every other test had
  deliberately used the preparation graph.

**Still open**

Chat has no retry on a dropped model call, which has now been hit in normal use.
The scheduled path — `oris-run-scheduled` writing a report, indexing it to
`/recall`, and acknowledging episodes — has not been run end to end.

Deterministic baseline: 301 passing, 17 skipped, clean lint and formatting.

### Selectable chat text — 2026-08-25

- **The conversation looked like it supported copying and never did.** Dragging
  across the chat pane visibly selected, and copying returned an empty string
  every time. Reported three times before it was diagnosed, twice by me from
  memory and wrongly: I first reasoned about the command line, which is not the
  affected front end, and then concluded from `RichLog.ALLOW_SELECT` being
  `True` that the support was already there.
- **`ALLOW_SELECT` means the widget joins the selection protocol, not that it
  can produce text.** Textual works out which characters a drag covers from
  `offset` metadata attached to each rendered segment by its own content
  pipeline. `RichLog` holds pre-rendered Rich output, which carries none, so the
  screen falls back to selecting the whole widget — and `Widget.get_selection`
  then finds nothing to extract, because it only reads `Text` or `Content`.
- **Measured rather than argued.** A drag across a `RichLog` returns `''`; the
  same drag across a `Static` returns the exact characters. Textual's own
  `Markdown` widget behaves like `Static`. That comparison is what settled the
  design; reading the attribute had pointed the opposite way.
- **The chat pane is now a `VerticalScroll` holding one widget per message** —
  `Static` for requests, errors and the command reference, Textual's `Markdown`
  widget for answers. Selection and `ctrl+c`/`cmd+c` are already bound by
  Textual, so no key handling or clipboard code was written.
- **The other three panes were fixed straight after, for the same reason.** The
  Activity tab's span detail, the evidence viewer and the prompt viewer were all
  logs too. Evidence and prompts exist precisely to be taken somewhere else — a
  ticket, an editor — so being unable to lift the text out defeated the point of
  having them. Each is now a single selectable block of text rather than a
  widget per line, because one `Static` already selects across its own lines.
- **The evidence viewer keeps its colours.** Rich's `Syntax` renders to segments
  that hold no character positions, so a drag over highlighted JSON selected
  nothing. Asking `Syntax` to *highlight* instead returns the same colouring as
  a `Text`, which selection can read. No colour was traded away for this.
- The test asserts on extracted text, not on widget types, and deliberately does
  not pin which character a given column lands on — that depends on padding,
  which is styling. What must hold is that a drag yields real characters from
  the answer and that copying sends exactly those.
- **Two things were wrong in the work that preceded this.** Two podcast files
  were committed earlier the same day without `ruff format`, and the history
  entry written afterwards claimed a clean formatting baseline. Both are fixed.

- **Pasting was dropped from the item, not deferred.** The original entry asked
  for copy and paste both. Asked directly, the requirement is only to select and
  copy answers out of the conversation pane; typing a URL into the input line
  was never the problem.

Deterministic baseline: 305 passing, 17 skipped, clean lint and formatting.

### Phoenix control from the interface — 2026-08-25

- **The Activity tab now says whether Phoenix is running, and can start, stop
  or restart it.** `s` toggles, `r` restarts. Both call the service functions
  `orisctl` already uses, so no launchd handling was written here.
- **An empty activity view had two causes that looked identical.** Either
  nothing ran, or the collector was down. They call for opposite responses, and
  the table could not tell them apart. The state now sits beside it.
- **The state is read from launchd on every refresh, never remembered.** The
  service can be started or stopped outside this interface, or die on its own.
  A toggle that trusted its own last action would then send the opposite
  command, and be confidently wrong about what it had just done.
- **"Not installed" is kept distinct from "stopped".** A stopped service starts
  with one key; one that was never installed cannot, and calling it stopped
  sends the reader looking for a problem that is not there. Both keys refuse
  early in that state and name the `orisctl phoenix install` that fixes it.
- **`launchctl` runs on a worker thread.** It is a subprocess and takes long
  enough to be noticed, so calling it inline froze the interface mid-keystroke.
- **A failure notifies and does nothing else.** Tracing is optional and the
  scheduler must never depend on Phoenix, so a collector that will not start is
  not a reason for the conversation to stop working. The test drives that
  directly: it fails a start, then asks a question and expects an answer.
- The "no traces" message pointed at `./start-phoenix.sh`. It still exists, but
  the key is now the shorter path and the message says so.
- Footer labels are deliberately short. The Activity tab already carries eight
  keys, and longer ones pushed this pair off the end at ordinary widths. What
  each key does is spelled out on the status line beside the state.

Deterministic baseline: 311 passing, 17 skipped, clean lint and formatting.

### Keeping MCP server logging off the screen — 2026-08-25

- **Running `/podcasts` in the terminal interface broke the display.**
  Net-Razor's JSON log lines appeared underneath the interface, scrolling the
  frame out from under it and leaving the conversation interleaved with
  somebody else's logs and the prompt no longer where it appeared to be.
- **Every stdio MCP server inherits ORIS's stderr.** That is how the transport
  works — stdin and stdout carry the protocol, and stderr is left to the
  server's own logging. Net-Razor writes a line per request there. Nothing was
  misconfigured on either side; the client is simply expected to decide where
  that goes, and ORIS had never decided.
- **This was never specific to podcasts.** ThreatSyft and every other stdio
  server had the same path to the terminal. Podcast runs made it obvious
  because they are long and chatty.
- **Redirected at the file descriptor, not by reassigning `sys.stderr`.** The
  MCP client takes `sys.stderr` as a default argument value, and Python binds a
  default once, when the module is imported. Rebinding the name afterwards
  leaves that default pointing at the original stream, so the child would still
  inherit the terminal. Verified before relying on it, and pinned by a test so
  that an upstream change making the simpler fix viable shows up as a failure
  rather than going unnoticed.
- **The first attempt redirected file descriptor 2, and broke the interface
  completely.** Textual draws every frame on `sys.__stderr__`, so redirecting
  one level below that moved the interface as well: `oris-tui` painted itself
  into the log file and the terminal stayed blank. Over SSH it looked like the
  command did nothing. Shipped without ever running the interface, on tests
  that only checked where a child process's output went.
- **The working fix separates the two names for that stream.** Textual uses
  `sys.__stderr__` and nothing touches it. The MCP client takes `sys.stderr` as
  a default argument value, bound once when its module is imported, so
  rebinding that name before the import decides where every server's logging
  goes without moving the interface. The import ordering is therefore
  load-bearing: `_main` reads settings from `oris.config`, because importing
  `web_research_app` first would pull in the client and bind the terminal.
- **The redirection is undone on the way out**, so a crash still prints its
  traceback where it can be read. Configuration is loaded before it starts, so
  a bad `.env` also still reports itself to the terminal. Server logging lands
  in `~/.oris/logs/mcp-servers.log`.
- **A test now pins that Textual draws on the stream this leaves alone**, and
  another that importing `oris.tui` does not load the MCP client. Either
  changing upstream would silently hide the interface again.
- **The lesson is about what was verified, not about stderr.** Both attempts
  had passing tests. The first one's tests were all about where a child
  process's output went, and every one of them was true — the interface was
  simply never run. A pseudo-terminal reproduction took two minutes once it was
  actually tried, and would have caught it before it shipped.

Deterministic baseline: 316 passing, 17 skipped, clean lint and formatting.

### Evidence for podcast runs — 2026-08-26

- **`e` found nothing on a podcast run.** Only Threat Intel wrote evidence, so
  the key that answers "what was this built from" answered it for one
  specialist out of five. A summary always prompts the question a summary
  cannot answer — what did the episode actually say — and the transcript was
  the one thing ORIS read and then threw away.
- **Every page a run reads is now kept**, and written to the store Threat Intel
  already uses. That is the whole point: both are recorded the same way,
  correlated to their run the same way, aged out on the same retention
  schedule, and deleted with the conversation the same way. One key, one
  mechanism, whichever specialist produced the run.
- **I recommended the opposite a turn earlier and changed my mind.** The first
  recommendation was to fetch the transcript back from Net-Razor on demand,
  because Net-Razor already stores it and a second copy is duplication. That is
  still true. What it was weighed against was wrong: fetching means the
  interface holding MCP tools, a page at a time, one subprocess per page, and
  evidence that cannot be read when Net-Razor is unavailable. A run's
  transcripts are a few hundred kilobytes on a retention schedule that already
  exists. The simplicity gate settles it.
- **The contract's rule was never against this.** It says full transcripts are
  never returned in public output or added to Local Knowledge — an evidence
  file is neither, and the reason for that rule was context cost and search
  pollution, neither of which applies.
- **Paging was designed and then dropped, on a measurement.** A viewer that
  fetched one part at a time was the plan until 540 KB of text was rendered in
  a single widget in 0.47 seconds. The whole run's transcripts open at once.
- **The viewer renders on shape.** Provider responses are JSON and read best as
  JSON. A transcript is prose, and JSON would show it as one enormous line with
  every newline written out as an escape — the same evidence, unreadable.
- **The backend is repeated in the viewer**, not just in the digest, because
  this is where someone comes to check a name the summary got wrong, and
  whether a machine transcribed it is the first thing that explains one.
- A run that produced no usable transcript stores nothing. An empty evidence
  file would claim a run was recorded when it was not.

Deterministic baseline: 325 passing, 17 skipped, clean lint and formatting.

### YouTube Catch-up removed — 2026-08-26

- **Net-Razor dropped its YouTube source, so the three tools ORIS asked for
  stopped existing.** Podcast Catch-up had been built as the replacement and was
  working well enough to settle it. Everything YouTube is deleted: the
  specialist, its two prompts, its tool tuple and loader, its builders, its
  scheduled job type, its run record, its report formatter, its runner, its
  tests, its two live contracts, its contract document, its routing line and its
  routing evaluation case.
- **The separation stated its own test and it held.** Every podcast test passed
  before a line of a podcast file was touched. What did need editing was shared
  infrastructure — the router enum, the scheduler dispatch, the tool loaders,
  the graph signature — which is where a second specialist was always going to
  appear. The only podcast edits afterwards were docstrings describing a module
  that no longer exists.
- **The four scheduled-run tests were ported, not deleted.** They cover
  persisting a report before acknowledging it, the empty-queue report, and both
  failure paths, and those invariants belong to podcasts exactly as they did to
  videos. Deleting them would have left the scheduled podcast path with no
  coverage at all — and that path has still never been run end to end, so it was
  the worst possible thing to leave untested.
- **The routing prompt no longer has to disambiguate.** It carried a rule about
  preferring one catch-up for "videos, channels, watching" and the other for
  "podcasts, episodes, shows, listening". With one catch-up left, a bare "catch
  me up" is no longer ambiguous and the rule is gone.
- The routing evaluation set swapped its YouTube case for the podcast one and
  its version was bumped, so an older report is not mistaken for the same set of
  questions.
- **Kept at the time:** `docs/youtube-transcription-plan.md`, already marked
  dead, which recorded why the Whisper-for-YouTube work stopped. Deleted on
  2026-08-26 in the sweep below; its reasoning survives in the entry above.
- Net removal: 27 files, 1,810 lines deleted against 115 added.

Deterministic baseline: 318 passing, 15 skipped, clean lint and formatting.

### The no-transcript caveat named the wrong thing — 2026-08-26

A real catch-up run came back as a wall of near-identical lines: one per
episode, each naming the episode and then telling the reader to "ask for this
show by name to have it transcribed."

Two faults in one message.

The name was wrong. Narrowing a catch-up to one show matches on the show's
display name, which is what Net-Razor returns as the episode's author. The
caveat printed the episode title instead, so the instruction it gave could
never be carried out. Typing the episode title back would match no show.

The repetition was wrong. Publishing no transcript is a fact about a feed, not
about an episode. A daily show with five new episodes stated it five times and
pushed every other caveat — the feed errors, the truncations — off the end of
what anyone would read. The caveats are now collected per show while the
episodes are walked and written once at the end, in first-seen order.

Both branches were changed, the one where transcription is absent entirely and
the one where chat declines to start it, because both said the same thing about
the same feed.

What this does not fix is why there was nothing to summarise. Chat deliberately
never transcribes a whole catch-up; it uses transcripts that already exist. The
nightly job that would create them has never been added to `schedules.toml`, so
on feeds that publish no transcript of their own, none ever exist. That is a
configuration gap, not a code one, and it is the reason the run was mostly
caveats.

Deterministic baseline: 319 passing, 15 skipped, clean lint and formatting.

### A run was handed somebody else's evidence — 2026-08-26

Two Community Research searches came back with nothing useful. Opening the
evidence for either one showed a podcast catch-up about the Detroit Pistons.

Community Research stores no evidence at all. Only Threat Intel and Podcast
Catch-up do. So the run genuinely had none — and the evidence key, finding none,
fell through to a fallback that opens the newest report in the whole store.
The newest report was the last podcast run.

The fallback itself is correct where it belongs. Typing `/threat show` with no
ID means "the most recent one", and that is the behaviour it was written for.
The mistake was letting a second, different question reach it. "No ID was
typed" and "this run collected none" have the same empty answer and completely
different right responses.

Showing the wrong run's data under the right run's name is worse than showing
nothing. Someone reading it has no way to know it is unrelated, and the whole
point of stored evidence is that it can be trusted to be what produced the
answer above it.

The key now says which specialists store evidence and stops.

Deterministic baseline: 320 passing, 15 skipped, clean lint and formatting.

### Recaps, and saying where a transcript came from — 2026-08-26

Two of the podcast items from the plan, done together because the second is
what makes the first legible.

**A scheduled run's work was unreachable the morning after.** The run marks its
episodes processed, and Net-Razor leaves processed episodes out of the queue
from then on. So an ordinary catch-up the next morning reported no new episodes
— correctly, because the scheduled run had already taken them — and nothing in
ORIS could ask for them back.

Net-Razor's episode tool has always accepted `include_processed`. ORIS passed a
hardcoded `false`. It is now part of the request, reached from chat by putting
`recap` in front of a `/podcasts` command, with or without a show name after
it. That gives all four of the intended paths: transcribe one show, transcribe
what is new, re-read one show, re-read everything already transcribed.

A recap reads and never writes, which took two guards rather than one. It does
not transcribe, because turning "show me last night's work" into another hour of
Whisper is not what was asked. And it does not acknowledge — that one is the
subtle half. A recap that happened to pick up a new episode with a publisher
transcript would otherwise mark it processed and take it out of the next real
catch-up without ever transcribing it.

**A machine transcript no longer looks like a publisher's, and neither looks
like the other one's age.** There are three states worth telling apart and only
two were represented. `transcript_backend` distinguishes `publisher` from
`whisper`, but a Whisper transcript from last night's scheduled run is served
straight from Net-Razor's store and arrives identical to one produced a minute
ago. Episodes now also carry whether this run made the transcript, which is
recorded where it is actually known: at the point the transcription tool is
called.

Both front ends now say it in words — "publisher's transcript", "transcribed by
ORIS during this run", "transcribed by ORIS earlier" — rather than printing a
backend name that only means something to someone who already knows. The wording
lives in one place because two copies of a phrase like that drift until they
contradict each other.

The per-episode "was machine-transcribed" caveat is now one line per run. Which
episodes it applies to is on each episode's own line, and repeating the warning
per episode was the same problem as the no-transcript caveat earlier the same
day: one fact, stated so many times that everything else was pushed off the end.

**Two things this exposed.** The chat episode list was bare numbered links built
from the digest's citations — no titles, no shows, and nothing at all when the
digest cited nothing, which is precisely when the caveat telling the reader to
"see the episode list below" fires. It now lists every episode the run covered.

And moving the rendering out revealed a layering fault. Putting it in the
specialist module meant chat imported the specialist, which imports the
Net-Razor tool names, which pulls in the MCP client — breaking the test that
asserts the terminal interface starts without it. The public output shape and
how to render it now live in their own module that imports nothing but `typing`.
The graph is not the output.

Deterministic baseline: 329 passing, 15 skipped, clean lint and formatting.

### Clearing out what was left of YouTube — 2026-08-26

The specialist was deleted earlier the same day. This is the sweep for what the
deletion did not reach: stored data, and documents still written as though it
existed.

**Trace data.** Three complete runs sat in the Phoenix database, eleven spans
between them, still showing up in the activity tab as turns belonging to a
specialist ORIS no longer has. Deleted by trace rather than by span, because
the schema cascades from the trace and deleting spans alone would have left
orphaned rows behind. Phoenix was stopped first and the database was copied
before anything was removed.

Worth recording how nearly this went wrong. A first pass matching "youtube"
anywhere in a span's attributes found 428 spans across 48 traces. Almost all of
them were podcast runs: a transcript that mentions YouTube, a model call
carrying that transcript, a search result linking to a video. Deleting on that
match would have destroyed most of the podcast trace history to remove three
dead runs. The right filter was the span's own name, which is ORIS's own graph
node and cannot be produced by anything a provider returned.

**Evaluation reports.** Three `youtube-catch-up-*.json` reports removed. The
routing reports were kept: four of them contain a `youtube_catch_up` expected
route, but they are the audit trail of routing accuracy generally, and one stale
case label is not a reason to delete a run's whole record. The routing set's
version was bumped when the case was swapped, so nothing will mistake an old
report for a current one.

One Web Research report also matches "youtube" — a video URL in a search result.
That is provider content, not our data, and stays.

**The archive and the conversation checkpoints were already clean.** Nothing in
either mentions YouTube. Nothing was deleted from them.

**Documents.** `docs/youtube-transcription-plan.md` deleted. The 2026-08-26
removal entry had kept it as history, which was the wrong call: it is a design
for functionality that no longer exists and cannot, and the reasoning worth
keeping is in the entry above it. Both podcast documents and both podcast module
docstrings were still framed as "a candidate replacement for YouTube Catch-up",
which is a description of something that has not been true since that specialist
was deleted. They now describe what Podcast Catch-up is. The capability boundary
no longer forbids YouTube tools, because there are none to forbid.

The history keeps every mention. That is what it is for.

This was done on the MacBook only. The Mac mini has its own trace database and
its own artifact directory, and neither has been touched.

Deterministic baseline: 329 passing, 15 skipped, clean lint and formatting.

### ORIS can name the shows it is configured with — 2026-08-27

Net-Razor's `doctor` output, read for an unrelated reason, showed that the local
checkout was well ahead of the Mac mini — and that it had already grown
`net_razor_podcast_feeds`, the tool the plan was about to ask for. It returns
each configured show by name, plus whether that show publishes transcripts, read
from its newest episode.

ORIS had no route to it. That mattered more than "a missing convenience": a
catch-up is narrowed by naming a show, the configuration is a list of feed URLs,
and Net-Razor is the only place a show's name exists. So ORIS could not answer
"which podcasts do you cover?" about its own setup, and naming one was guesswork
against a name nobody had ever been shown.

`/podcasts list` answers it.

**The tool is loaded separately and kept off the catch-up allowlist.** A catch-up
must not be able to call it. It reads every configured feed, which costs about a
second, and a catch-up already learns each show's name from the episodes it
fetches — so the call would be pure cost. This is the same split transcription
already uses, and for the same reason: the allowlist is what a specialist can
reach, not a list of everything the server offers.

**Listing branches at the graph entry.** It shares none of a catch-up's work, so
a flag checked inside every node downstream would have been five checks for one
decision.

**It makes no model call.** The answer is a list Net-Razor already has, and
asking a model to restate it could only introduce a show that does not exist.

**The `publishes_transcripts` hint is shown, not acted on.** It would be
tempting to use it to skip straight to Whisper for a show that publishes
nothing. That would be wrong twice over: it reads the newest episode only, and
Net-Razor's transcript store is first-writer-wins, so transcribing an episode
whose publisher transcript was never fetched forecloses the better version
permanently. The hint tells a reader which show will be slow to ask about. The
decision stays per episode, on what the publisher actually returned.

Deterministic baseline: 334 passing, 15 skipped, clean lint and formatting.

### Community Research was capped at 150 words — 2026-08-27

Running Net-Razor's `research` tool directly from an editor produced noticeably
more useful summaries than asking ORIS the same thing. Same tool, same JSON, far
thinner answer. The cause was entirely in ORIS: the prompt said "write two to
four concise bullet points and no more than 150 words", over a token budget that
allowed roughly 380.

The 150 words are the wrong shape for what this specialist reads. Net-Razor
returns up to ten posts per source, each with its own claim, and the raw result
never survives the graph — `run_community_research` keeps the answer text and
the cited URLs and discards the evidence. Anything not written into those 150
words has to be paid for again with a second fan-out.

**The length rule is now under 400 words with the budget raised to 1024
tokens.** The ceiling still exists, because a bounded answer is the point of a
synthesis step, but it no longer discards most of the space it was given.

**Length alone would not have fixed it.** "Concise" is a shape instruction, not
a quantity, and a model given more room to be vague uses it. So the prompt now
borrows what Threat Intel already does: one bullet per post that carries a
point, name the source it came from, and report the actual claim, number, name
or version rather than characterising it — with a worked example of a useful
bullet against a useless one.

**"If the evidence is insufficient, say so" is gone.** Replaced with Threat
Intel's "say what is uncertain and what would resolve it". The first invites a
one-line dead end. Web Research and Local Knowledge still carry the old line;
this is the first of the three.

The evaluation case for a well-covered topic asked for "concise bullets", which
would have scored the old behaviour as correct. It now asks for the specific
claims each post carried.

### Web Research had three sentences to say everything — 2026-08-27

Same problem as Community Research, one step worse. The entire length rule was
"Write no more than three sentences", for the specialist that reads up to five
web sources over a budget that allowed roughly 380 words. The prompt threw away
about ninety per cent of the space it had.

The cost is not just brevity. `run_web_research` keeps the answer text and the
source links and discards the search results, so a detail left out of those
three sentences cannot be recovered without paying Tavily for the search again.

**Now under 400 words at 1024 tokens, matching Community Research.** Both
specialists do structurally the same job — read a fan-out, write one bounded
answer, keep nothing — so their ceilings should not have differed by a factor of
five for no recorded reason.

**The specificity rules come from Threat Intel**, which had them already: report
what the source actually says rather than characterising it, include the version
numbers and dates and exact classifications it carried, and say which source
carried which claim where they disagree. The worked example uses the Python
security-release case from the evaluation set, because "a recent Python security
update was released" is exactly the answer the old prompt rewarded.

**One rule was added that Threat Intel does not have:** say plainly when the
evidence does not cover part of the question. A three-sentence budget makes
answering the easy half and stopping the cheapest possible behaviour, and
nothing in the old prompt discouraged it.

"If the evidence is insufficient, say so" is replaced here too. Local Knowledge
is now the only specialist still carrying the line.
