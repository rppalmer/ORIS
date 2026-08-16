# ADR 002: Project-owned local scheduling

- Status: Accepted
- Date: 2026-07-27
- Last reviewed: 2026-08-14

## Context

ORIS will run several independent jobs at different times on an
always-on Mac mini. Schedule definitions should remain visible, reviewable, and
portable with the project instead of being scattered across machine-specific
`launchd` files.

The current oMLX DMG, project checkout, virtual environment, and secrets belong
to the interactive macOS user. A per-user LaunchAgent is therefore the simplest
safe way to validate local scheduling, but dependence on an active user login
is explicitly unacceptable for the long-term Mac mini runtime.

LangGraph's official cron API belongs to LangSmith Deployment. It is the
preferred long-term option when ORIS runs on persistent Agent Server
infrastructure, but it would add unnecessary infrastructure to the current
single-machine deployment.

## Decision

### Short term: local scheduler

- The project-root `schedules.toml` file will be the source of truth for job
  definitions and cron expressions.
- Python's standard-library `tomllib` will read the file. Existing Pydantic
  models will validate the complete configuration before any job is scheduled.
- Jobs will select an allowlisted task name such as `web_research`. The file
  may not contain shell commands, Python import paths, credentials, or arbitrary
  tool names.
- Jobs may declare allowlisted task inputs such as
  `date_window="previous_day"` and `search_category="news"`. The application
  resolves calendar windows from the configured schedule time zone and passes
  absolute dates to the specialist; the model does not control scheduled date
  boundaries or search category.
- The stable APScheduler 3.11.x series provides cron parsing, time-zone
  handling, normal per-job concurrency behavior, and execution events. The 4.x
  prerelease does not provide a benefit required by this single-process design.
  APScheduler is a direct runtime dependency; its local in-memory runtime is
  implemented.
- A single APScheduler process will recreate all jobs from `schedules.toml` at
  startup. The schedule file, rather than an APScheduler database, remains the
  authoritative job store.
- Executions missed while the scheduler is offline will be skipped. No
  persistent APScheduler job store or custom catch-up state will be added.
- During initial development, machine-specific LaunchAgents in
  `~/Library/LaunchAgents/` supervise ORIS's services in the logged-in user's
  context. `launchd` will not contain individual task schedules.
- Every service is rendered by one set of rules, so none has its own path
  convention: a label built from the service name, a repository-owned template
  at `launchd/com.rppalmer.oris.<service>.plist.template`, an absolute
  executable inside the project's own virtual environment, and a pair of logs
  named after the service under the project-owned `logs/` directory. A test
  asserts that rule across every service rather than leaving it to be
  remembered. `orisctl <service> <action>` renders the plist and provides
  install, uninstall, start, stop, restart, and status using `launchctl
  bootstrap`, `bootout`, `kickstart`, and `print`.
- A supervised program must be the thing being supervised, not a wrapper in
  front of it. Launching the trace collector through `uvx` made the collector a
  child of the supervised process, so a stop killed the wrapper and left the
  collector holding its port; the replacement then crash-looped under
  `KeepAlive`. Services are launched through their own console scripts.
- Services are independent. The scheduler runs the two services' health apart:
  a stopped collector neither stops nor fails a scheduled run.
- The generated plist contains absolute paths and the non-secret
  `PYTHONUNBUFFERED` setting only. Runtime configuration and credentials remain
  in `.env`; they are not copied into the plist.
- Configuration changes will require validation and a controlled scheduler
  restart; automatic file watching is deferred.
- Scheduled runs will invoke a named specialist directly rather than asking a
  model to choose a route. Interactive and scheduled requests may overlap;
  ORIS will rely on oMLX batching rather than add a global model lock.
- On `SIGINT` or `SIGTERM`, the scheduler will stop starting due jobs, wait for
  an active run to finish, and then exit. Forced termination recovery remains
  deferred unless it becomes a measured problem.

### Result retention

Interactive CLI requests are not durable output artifacts. Their results are
printed to the terminal and their conversation state is retained by the
official SQLite checkpointer. They may also appear in a short-lived Phoenix
trace when tracing is enabled.

Scheduled jobs are unattended, so every attempted run will retain a small
durable history record containing its job ID, run ID, timestamps, outcome, and
error summary when applicable. Each successful run will additionally write a
timestamped Markdown artifact under `artifacts/scheduled/<job-id>/`. A
scheduled artifact is the job's deliverable; the run record is its operational
history. Exact search category and date bounds are retained with the run record.
Neither is a checkpoint, agent memory, or diagnostic trace. The initial policy
keeps all run records and reports.

That growth was measured on 2026-08-13 and automatic deletion was declined:
everything ORIS owns came to 2.4 MB, against 36 MB for Phoenix, which already
enforces its own 14-day policy. The policy stays "keep everything" until the
archive passes a few hundred megabytes.

Incremental collection jobs that acknowledge provider-owned work use a stricter
completion boundary. The runner first writes and indexes the validated report,
then sends the provider's idempotent acknowledgement, and only then marks the
run successful. If acknowledgement fails, the failed run retains its report and
report path. A later run may repeat the same material; this is preferable to
losing a deliverable after the provider marked the source processed. The local
files, knowledge database, and provider database do not form one atomic
transaction, and the first implementation adds no reconciliation service.

Conversation persistence is separate from scheduling and uses the official
LangGraph SQLite checkpointer. The separate local knowledge repository makes
completed chat turns and scheduled artifacts searchable without treating
checkpoints or traces as the search corpus.

### Required migration: headless Mac mini

The LaunchAgent is transitional. Before ORIS is considered a reliable
always-on Mac mini service, remove the dependency on a logged-in user:

- run oMLX through a supported headless startup mechanism so its API is
  available after boot without an interactive login;
- install the ORIS scheduler as a system LaunchDaemon under
  `/Library/LaunchDaemons/`;
- run ORIS as a dedicated, non-root service identity rather than as
  `root`;
- move the application, environment configuration, logs, and artifacts to
  stable locations with permissions owned by that service identity;
- update the management script to perform explicit privileged installation and
  removal; and
- verify the complete path with a reboot test performed without logging in.

This migration changes process supervision and filesystem ownership only. It
must not change `schedules.toml`, job contracts, or specialist graph behavior.

### Long term: deployment scheduler

If ORIS moves to persistent self-hosted or managed Agent Server
infrastructure, replace the local APScheduler and `launchd` runtime with the
official LangGraph cron API. Scheduled inputs will continue to target named
specialist graphs directly. The graph contracts and job runner must not depend
on the scheduling backend.

At that point, migrate or synchronize the desired schedules from
`schedules.toml` into deployment cron resources and use the deployment's run
and thread retention controls. Do not operate two authoritative schedulers at
the same time.

## Consequences

- Multiple schedules remain centralized and reviewable in the project.
- The Python scheduling code remains portable; only process supervision is
  macOS-specific.
- Initial scheduling depends on the development user being logged in, and on
  the host being awake. On 2026-08-14 the `weekday-ai-news` job did not fire:
  the scheduler process was alive and had never exited, but the MacBook was
  asleep at the trigger time. A missed execution on a sleeping host is the same
  case as a missed execution on a stopped scheduler and is skipped by the same
  rule, deliberately. Both are recorded limitations of running on a laptop, and
  both are resolved by the LaunchDaemon migration onto the always-on Mac mini.
- The LaunchAgent tooling, installation, and lifecycle operations have been
  verified on the development machine. A timed unattended execution has also
  produced the expected run history, report, and knowledge document. Only
  login/reboot recovery remains as an operational acceptance check.
- Web Research and YouTube Catch-up both use the same project-owned scheduler
  and run-history boundary. YouTube additionally preserves provider processing
  receipts until its report is durable and searchable, then acknowledges them.
- The local runtime adds one idle Python scheduler process, but not another
  model process.
- Recreating schedules at startup avoids two competing job-definition stores.
- Runs missed while the scheduler is stopped are intentionally not replayed.
- Overlapping callers may affect latency, but oMLX batching avoids custom
  application-level locking until a measured problem exists.
- Scheduled output becomes a durable artifact, while ad-hoc chat remains
  durable conversation state rather than an output artifact.
- Moving to enterprise scheduling replaces the trigger layer without rewriting
  specialist graphs.

## References

- [LangGraph cron jobs](https://docs.langchain.com/langsmith/cron-jobs)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [APScheduler user guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
- [APScheduler releases](https://pypi.org/project/APScheduler/)
