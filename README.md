# ORIS

ORIS—**O**rchestration + **R**esearch + analys**IS**—is a private, local-first
assistant built with Python and LangGraph. Its goal is to handle ad-hoc
questions and scheduled research with predictable workflows, cited evidence,
durable local history, and local traces that make its activity easy to inspect.

The current application is a command-line assistant and web researcher. It can
chat directly, research the open web, collect community information from X and
Hacker News, summarize recent videos from configured YouTube channels, and
search earlier conversations and scheduled reports. It is read-only with
respect to external systems.

## What is built

- A constrained LangGraph router selects one of five fixed paths: direct chat,
  Web Research, Community Research, YouTube Catch-up, or Local Knowledge.
- Web Research performs one bounded Tavily search and returns a cited answer.
- Community Research uses the local Net-Razor MCP server to collect bounded X
  and Hacker News evidence.
- YouTube Catch-up uses Net-Razor to discover recent videos, retrieves and
  summarizes transcripts one at a time, and produces a cited digest.
- SQLite stores resumable chat sessions and a separate searchable archive of
  successful chat exchanges and scheduled reports.
- APScheduler runs validated jobs from the project-owned `schedules.toml` file
  and writes timestamped Markdown reports and JSON run records.
- Optional Phoenix tracing stays on the local machine. LangSmith tracing is
  disabled by configuration.

The model is accessed through oMLX's OpenAI-compatible API. The model name and
server address are configuration, so the same application can run on the
development MacBook or move to the Mac mini without source-code changes.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- An accessible oMLX server with a compatible instruction model loaded
- A Tavily API key
- A local [Net-Razor](https://github.com/rppalmer/net-razor) checkout for
  Community Research and YouTube Catch-up

Net-Razor and Phoenix are optional if their related capabilities are not used.
The Tavily setting is currently part of the required application configuration.

## Setup

Install the project and create the local configuration:

```shell
uv sync
cp .env.example .env
```

Edit `.env` with your own values. Do not commit that file.

| Setting | Purpose |
| --- | --- |
| `LOCAL_LLM_BASE_URL` | oMLX API base URL reachable from the machine running ORIS |
| `LOCAL_LLM_MODEL` | Model ID reported by oMLX |
| `LOCAL_LLM_API_KEY` | Local oMLX API credential |
| `LOCAL_LLM_MAX_HISTORY_TOKENS` | Conversation tokens sent per turn; raise it for a larger context window |
| `TAVILY_API_KEY` | Tavily credential for Web Research |
| `NET_RAZOR_PYTHON_EXECUTABLE` | Absolute path to Net-Razor's virtual-environment Python |
| `THREATSYFT_PYTHON_EXECUTABLE` | Absolute path to ThreatSyft's virtual-environment Python |
| `THREATSYFT_ROOT` | Absolute path to the ThreatSyft checkout |
| `ORIS_THREAT_REPORT_RETENTION_DAYS` | Days to keep full Threat Intel evidence reports (default 30) |
| `LOCAL_TRACING_ENABLED` | Set to `true` only when local Phoenix tracing is wanted |

Keep `LANGSMITH_TRACING=false`. Storage paths and the Phoenix endpoint have
working local defaults in `.env.example` and can be changed when moving the
application to another machine.

## Use the assistant

Start the command-line interface from the project root:

```shell
uv run oris
```

Ordinary messages use the constrained router. These commands bypass it when
you want an explicit path:

- `/research <question>` — search the open web with Tavily.
- `/community <topic>` — research the previous day on X and Hacker News.
- `/recall <question>` — search earlier successful chats and scheduled reports.
- `/threat report <anything>` — return the collected evidence pivoted by field
  instead of a written summary. No model call, so nothing is lost to summarising.
  Composes with the keywords below: `/threat report enrich <ip>`.
- `/threat <question>` — defensive ThreatSyft lookup. One constrained model call
  picks the capability. Never chosen by the router; you must ask for it.
- `/threat enrich <indicator>` — force indicator reputation only. Egresses the
  indicator to third-party providers and consumes API credits.
- `/threat ref <term>` — force defensive reference lookup only across ATT&CK,
  KEV, LOLBAS, and NVD. Stays local; nothing egresses. `mitre` is an alias.
- `/threat show <id> [source]` — print a stored evidence report in the
  terminal, optionally one provider only. Never sent to the model, so a
  large report costs nothing in context.
- `/help` — show the command list.
- `/session` — show the active conversation ID.
- `/new` — start a blank conversation without deleting older sessions.
- `/exit` — stop the application.

The CLI invokes the compiled graph directly; a LangGraph development server is
not required. A failed request reports the component and reason, then returns
to the prompt without adding the failed turn to chat history or Local
Knowledge.

## Local data

- `data/checkpoints.sqlite` stores durable conversation state.
- `data/current_session` identifies the conversation resumed at startup.
- `data/knowledge.sqlite` indexes successful chats and scheduled reports for
  `/recall`.
- `artifacts/scheduled/<job-id>/` contains scheduled Markdown reports and JSON
  run records.
- `artifacts/evaluations/` contains retained live-evaluation results.
- `artifacts/threat/` contains full Threat Intel evidence reports, named
  `<timestamp>-<id>-<subject>.json`. They are deleted after
  `ORIS_THREAT_REPORT_RETENTION_DAYS` (default 30); copy one elsewhere to
  keep it. The sweep runs when a report is written.
- `traces/phoenix/` contains local Phoenix data when tracing is enabled.

Ad-hoc answers are kept in the conversation and searchable knowledge database;
they are not written as separate Markdown reports. Older sessions are not
automatically inserted into a new session's context.

## Scheduled jobs

`schedules.toml` is the single source of truth for recurring jobs. It currently
contains an enabled weekday Web Research job. The scheduler also supports
bounded YouTube Catch-up jobs; their channel list remains in Net-Razor.

Run one enabled job immediately by its configured ID:

```shell
uv run oris-run-scheduled weekday-ai-news
```

Run all enabled schedules in the foreground:

```shell
uv run oris-scheduler
```

On macOS, the transitional per-user LaunchAgent can supervise the scheduler:

```shell
uv run orisctl scheduler render
uv run orisctl scheduler install
uv run orisctl scheduler status
uv run orisctl scheduler stop
uv run orisctl scheduler start
uv run orisctl scheduler restart
uv run orisctl scheduler uninstall
```

Inspect the rendered plist before installation. This LaunchAgent requires its
user to be logged in; moving to a dedicated, headless Mac mini LaunchDaemon is
a documented future deployment step.

## Local tracing

Start Phoenix in a separate terminal:

```shell
./start-phoenix.sh
```

Set `LOCAL_TRACING_ENABLED=true` in `.env`, restart ORIS, and open
`http://127.0.0.1:6006`. ORIS does not start Phoenix automatically,
and requests made while tracing is disabled are not added retroactively.
Phoenix binds to loopback, disables telemetry, stores data under
`traces/phoenix/`, and retains it for 14 days.

## Development

Run the deterministic checks before committing changes:

```shell
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

Live contracts under `tests/live/` are opt-in because they contact the local
model or external services. The full Web Research evaluation can also be run
with `uv run python -m oris.evaluation`; it consumes Tavily credits
and retains its results for review. Task-specific system prompts are
version-controlled in `src/oris/prompts/` and are loaded when the
application starts.

`langgraph.json` exposes `oris`, `web_research`, and
`community_research` for local LangGraph development-server or Studio use. The
normal CLI and scheduler do not depend on that server.

## Project documentation

- [Active implementation plan](docs/implementation-plan.md)
- [Implementation history](docs/implementation-history.md)
- [Portable local-first architecture](docs/architecture/001-portable-local-first-foundation.md)
- [Scheduling architecture](docs/architecture/002-project-owned-scheduling.md)
- [YouTube Catch-up contract](docs/youtube-catch-up-contract.md)
- [Future Web Evidence MCP plan](docs/web-evidence-mcp-plan.md)
