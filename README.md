# ORIS

ORIS—**O**rchestration + **R**esearch + analys**IS**—is a private, local-first
assistant built with Python and LangGraph. Its goal is to handle ad-hoc
questions and scheduled research with predictable workflows, cited evidence,
durable local history, and local traces that make its activity easy to inspect.

The current application is a local assistant and web researcher, usable from a
plain command line or a tabbed terminal interface. It can
chat directly, research the open web, collect community and preprint
information from X, Hacker News and arXiv, summarize recent episodes from
configured podcast feeds, and search earlier conversations and scheduled
reports. It is read-only with respect to external systems.

## What is built

- A constrained LangGraph router selects one of five fixed paths: direct chat,
  Web Research, Community Research, Podcast Catch-up, or Local Knowledge.
- Web Research performs one bounded Tavily search and returns a cited answer.
- Community Research uses the local Net-Razor MCP server to collect bounded X,
  Hacker News, and arXiv evidence.
- Podcast Catch-up uses Net-Razor to discover recent episodes from configured
  feeds, prefers the publisher's own transcript, and falls back to local Whisper
  transcription. Every episode says which it was, because machine transcription
  mangles the names and version numbers a digest then repeats as fact. A
  scheduled run may transcribe its whole budget; in chat only a named show does,
  because that is one episode rather than five.
- Threat Intel runs bounded defensive ThreatSyft lookups behind the explicit
  `/threat` command and stores the full evidence for every run. The router never
  selects it, because enrichment sends indicators to third-party providers.
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
  Community Research and Podcast Catch-up
- `ffmpeg` on `PATH` and Apple Silicon, for podcast transcription only. Without
  them Net-Razor reports `not_configured` and episodes with no published
  transcript become caveats.

Net-Razor and Phoenix are optional if their related capabilities are not used.
The Tavily setting is currently part of the required application configuration.

## Setup

Install the project and create the local configuration:

```shell
uv sync
mkdir -p ~/.oris
cp .env.example ~/.oris/.env
chmod 600 ~/.oris/.env
```

Edit `~/.oris/.env` with your own values. It lives outside the checkout on
purpose: ORIS reads it from there whatever directory it is started in, so the
interactive session and the scheduler always agree about which conversation
history and knowledge index are live.

| Setting | Purpose |
| --- | --- |
| `LOCAL_LLM_BASE_URL` | oMLX API base URL reachable from the machine running ORIS |
| `LOCAL_LLM_MODEL` | Model ID reported by oMLX |
| `LOCAL_LLM_API_KEY` | Local oMLX API credential |
| `LOCAL_LLM_TIMEOUT_SECONDS` | Ceiling on one model call (default 120) |
| `LOCAL_LLM_MAX_HISTORY_TOKENS` | Conversation tokens sent per turn; raise it for a larger context window |
| `TAVILY_API_KEY` | Tavily credential for Web Research |
| `NET_RAZOR_PYTHON_EXECUTABLE` | Absolute path to Net-Razor's virtual-environment Python |
| `THREATSYFT_PYTHON_EXECUTABLE` | Absolute path to ThreatSyft's virtual-environment Python |
| `THREATSYFT_ROOT` | Absolute path to the ThreatSyft checkout |
| `ORIS_THREAT_REPORT_RETENTION_DAYS` | Days to keep full Threat Intel evidence reports (default 30) |
| `LOCAL_TRACING_ENABLED` | Set to `true` only when local Phoenix tracing is wanted |
| `LANGSMITH_TRACING` | Must stay `false`; a true value is rejected at startup |
| `PHOENIX_COLLECTOR_ENDPOINT` | Where traces are sent; the Phoenix UI address is derived from it (default `http://127.0.0.1:6006/v1/traces`) |
| `PHOENIX_WORKING_DIR` | Where Phoenix keeps its data; the terminal interface reads its traces from here (default `~/.oris/traces/phoenix`) |
| `ORIS_CHECKPOINT_DB_PATH` | Conversation state (default `~/.oris/data/checkpoints.sqlite`) |
| `ORIS_KNOWLEDGE_DB_PATH` | The `/recall` archive (default `~/.oris/data/knowledge.sqlite`) |
| `ORIS_THREAT_REPORT_DIR` | Stored Threat Intel evidence (default `~/.oris/artifacts/threat`) |

Every storage path already defaults to somewhere under `~/.oris` and needs no
configuration; the last four exist to point an installation at directories it
already has. A leading `~` is expanded. Exported activity is always written to
`~/.oris/artifacts/exports` and does not follow `ORIS_THREAT_REPORT_DIR`.

## Installing on another machine

The same application runs on the development MacBook or the Mac mini through
configuration only. Nothing below is a code change.

```shell
brew install uv ffmpeg

mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/rppalmer/net-razor.git
git clone https://github.com/rppalmer/ORIS.git

cd ~/Projects/net-razor && uv sync --extra whisper
cd ~/Projects/ORIS && uv sync --extra tui
```

`uv` fetches the pinned Python. `ffmpeg` and the `whisper` extra are only needed
for podcast transcription, and the extra is Apple Silicon only.

Configuration lives outside both checkouts and is not in git, so copy it across:

```shell
# from the machine that already works
scp ~/.oris/.env              <host>:~/.oris/.env
scp ~/.net-razor/.env         <host>:~/.net-razor/.env
scp ~/.net-razor/podcasts.txt <host>:~/.net-razor/

# on the new machine
mkdir -p ~/.oris ~/.net-razor
chmod 600 ~/.oris/.env ~/.net-razor/.env
```

Three settings in `~/.oris/.env` are machine-specific and must be checked:

| Setting | What to do |
| --- | --- |
| `NET_RAZOR_PYTHON_EXECUTABLE` | Repoint at the new checkout's `.venv/bin/python`. It must be absolute; a relative path is rejected at startup. |
| `THREATSYFT_PYTHON_EXECUTABLE`, `THREATSYFT_ROOT` | Repoint or remove. Threat Intel then does not resolve; nothing else is affected. |
| `LOCAL_LLM_BASE_URL` | Point at `http://127.0.0.1:8000/v1` when oMLX runs on the same machine. A model call has no retry, so removing the network hop removes a way for a whole run to fail. |

Verify cheapest first:

```shell
cd ~/Projects/ORIS
uv run pytest -q
uv run oris
```

Then `/podcasts linux unplugged`. It exercises discovery, the transcript path,
paging, and the digest against a single episode, so a broken install fails in
seconds. Pick a show that publishes its own transcript for this: naming one that
does not will transcribe it, which works but takes minutes. Follow it with a
bare `/podcasts` for the full catch-up, which never transcribes.

### What does not come with the code

Everything ORIS knows lives in `~/.oris` on the machine it runs on. A new
install starts empty, and from that moment the two machines diverge.

- `~/.oris/data/` — conversations and the `/recall` archive.
- `~/.net-razor/data/` — every cached transcript and the list of episodes
  already acknowledged. Copying it means the new machine does not re-transcribe
  work already done.

Copy them only as a deliberate decision about which machine is the system of
record, not as part of routine setup.

### Remote use

There is no server and nothing to connect to. Both front ends are terminal
programs talking to a local SQLite database, so working on another machine means
an SSH session:

```shell
ssh <host> -t 'tmux new -A -s oris'
cd ~/Projects/ORIS && uv run oris-tui
```

`textual` renders fully over SSH. `tmux new -A` reattaches to the same session
each time, so a dropped connection does not kill a long turn.

## Use the assistant

Start the command-line interface from the project root:

```shell
uv run oris
```

There is also a tabbed terminal interface over the same graph; see
[Terminal interface](#terminal-interface).

Ordinary messages use the constrained router. These commands bypass it when
you want an explicit path:

- `/research <question>` — search the open web with Tavily.
- `/community <topic>` — research the previous day on X and Hacker News, and
  the previous week on arXiv.
- `/recall <question>` — search earlier successful chats and scheduled reports.
- `/podcasts` — catch up on new episodes from every configured feed. One
  episode per feed before a second from any, so a show that publishes daily
  cannot crowd out one that publishes weekly.
- `/podcasts <show>` — the newest episode of one configured show, summarised on
  its own. Matched on the show's name, so no feed URL is needed. This is the one
  chat command that will transcribe: a named show is a single episode, and most
  feeds publish no transcript, so refusing would make the command useless.
  Expect minutes rather than seconds when it has to.
- `/threat report <anything>` — return the collected evidence pivoted by field
  instead of a written summary. No model call, so nothing is lost to summarising.
  Composes with the keywords below: `/threat report enrich <ip>`.
- `/threat <question>` — defensive ThreatSyft lookup. One constrained model call
  picks the capability. Never chosen by the router; you must ask for it.
- `/threat enrich <indicator>` — force indicator reputation only. Egresses the
  indicator to third-party providers and consumes API credits.
- `/threat ref <term>` — force defensive reference lookup only across ATT&CK,
  KEV, LOLBAS, and NVD. Stays local; nothing egresses. `mitre` is an alias.
- `/threat show [id] [source]` — print stored evidence in the terminal, newest
  report by default, optionally one provider only. Every `/threat` run stores
  its evidence, so this always works. Never sent to the model, so a large
  report costs nothing in context.
- `/help` — show the command list. `/help <command>` or `<command> --help`
  narrows it to one command.
- `/session` — show the active conversation ID.
- `/new` — start a blank conversation without deleting older sessions.
- `/exit` — stop the application.

The CLI invokes the compiled graph directly; a LangGraph development server is
not required. A failed request reports the component and reason, then returns
to the prompt without adding the failed turn to chat history or Local
Knowledge.

## Local data

Your own data lives under `~/.oris`, not in the checkout, so that it survives a
re-clone and is the same data whatever directory ORIS was started in:

- `~/.oris/data/checkpoints.sqlite` stores durable conversation state.
- `~/.oris/data/current_session` identifies the conversation resumed at startup.
- `~/.oris/data/knowledge.sqlite` indexes successful chats and scheduled reports
  for `/recall`.
- `~/.oris/artifacts/threat/` contains full Threat Intel evidence reports, named
  `<timestamp>-<id>-<subject>-<conversation>.json`. Deleting a conversation in
  the terminal interface deletes the reports it produced. Otherwise they are
  deleted after `ORIS_THREAT_REPORT_RETENTION_DAYS` (default 30); copy one
  elsewhere to keep it. The sweep runs when a report is written.
- `~/.oris/artifacts/exports/` contains activity exported from the terminal
  interface.
- `~/.oris/traces/phoenix/` contains local Phoenix data when tracing is enabled.
  The terminal interface reads `phoenix.db` from here, read-only.

Two directories stay in the checkout, because they belong to the project rather
than to you and are read alongside `schedules.toml` and `evaluations/`:

- `artifacts/scheduled/<job-id>/` contains scheduled Markdown reports and JSON
  run records.
- `artifacts/evaluations/` contains retained live-evaluation results.

Ad-hoc answers are kept in the conversation and searchable knowledge database;
they are not written as separate Markdown reports. Older sessions are not
automatically inserted into a new session's context.

## Scheduled jobs

`schedules.toml` is the single source of truth for recurring jobs. It currently
contains an enabled weekday Web Research job. The scheduler also supports
bounded Podcast Catch-up jobs; the feed list remains in Net-Razor.

```toml
[[jobs]]
id = "nightly-podcasts"
enabled = true
cron = "0 6 * * *"
task = "podcast_catch_up"
days = 1
max_episodes = 5
```

`max_episodes` and `cron` are related numbers. A scheduled run is the only one
that transcribes a whole catch-up, and a run that transcribes its whole budget
can take a long time; a job still running when its next firing is due has that
firing skipped in silence. A scheduled run also acknowledges its episodes,
which is one-way — they leave the queue and do not come back.

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
user to be logged in and the machine to be awake: a job whose time passes while
the host is asleep is skipped, not caught up, exactly as a job missed while the
scheduler was stopped is skipped. That makes a laptop a poor scheduler host.
Moving to a dedicated, headless Mac mini LaunchDaemon is a documented future
deployment step.

## Local tracing

Phoenix is installed separately from ORIS, pinned, because ORIS talks to it
over the network and never imports it:

```shell
uv tool install "arize-phoenix==19.6.0"
```

Run it as a supervised service, which is the same command shape the scheduler
uses and survives closing whichever front end you started it from:

```shell
uv run orisctl phoenix install
uv run orisctl phoenix status
uv run orisctl phoenix restart
uv run orisctl phoenix stop
uv run orisctl phoenix uninstall
```

Both services render an absolute executable inside `.venv/bin`, log to
`logs/<service>.stdout.log`, and are otherwise independent: stopping Phoenix
does not affect the scheduler, and the collector being down never fails a run.

Or start it in the foreground for one session:

```shell
./start-phoenix.sh
```

Set `LOCAL_TRACING_ENABLED=true` in `~/.oris/.env`, restart ORIS, and open
`http://127.0.0.1:6006`. ORIS does not start Phoenix automatically,
and requests made while tracing is disabled are not added retroactively.
Phoenix binds to loopback, disables telemetry, stores data under
`~/.oris/traces/phoenix/`. Retention is a 14-day rule swept by a weekly job, so
traces can legitimately be up to about three weeks old.

## Development

Run the deterministic checks before committing changes:

```shell
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

Live contracts under `tests/live/` are opt-in because they contact the local
model or external services.

Evaluation sets live in `evaluations/`, one file per specialist, and are run
with `uv run python -m oris.evaluation <specialist>` — `web_research` (the
default), `local_knowledge`, `community_research`, or `threat_intel`. Each run
contacts the real services that specialist depends on, so it consumes Tavily
credits or provider lookups, and Threat Intel additionally stores an evidence
report. Reports are written to `artifacts/evaluations/` for human review; there
is no automatic scoring, and the way to judge a prompt change is to put two
reports on the same case file side by side.

Task-specific system prompts are version-controlled in `src/oris/prompts/` and
are loaded when the application starts.

## Terminal interface

A tabbed terminal interface runs the same graph as `oris`, parses the same
commands, and writes to the same archive. It is an alternative front end, not a
second application.

```shell
uv sync --extra tui
uv run oris-tui
```

**Chat** is the working surface: past sessions on the left, named by their most
recent request so the name agrees with the time beside it, and the conversation
on the right. Selecting a session replays it and continues it, and that choice is
written to `data/current_session`, so `oris` and `oris-tui` resume the same
conversation. The up arrow recalls earlier requests from the session itself.

`d` deletes the highlighted session after a confirmation that states what goes:
the conversation *and* every answer it contributed to `/recall`, since a
searchable answer belonging to a conversation that no longer exists is not what
deleting a conversation means. It cannot be undone. Phoenix traces are not
touched — they are Phoenix's data, under its own 14-day retention, and ORIS only
ever reads them. Deleting the session you are in starts a fresh one. The command
line has no equivalent; it has no list to select from.

**Activity** answers what a turn actually did, from the traces Phoenix already
collected: which node ran, how deeply nested, how long it took, and how many
tokens it spent. It lists the current session's runs by default.

| Key | Action |
| --- | --- |
| `F1` / `F2` | Chat / Activity |
| `d` | Delete the highlighted session (Chat tab, after confirming) |
| `F5` | Reload traces |
| `a` | Toggle between this session and every session |
| `p` | Show the system prompt each model call in the selected run was given |
| `e` | Open the full JSON evidence behind the selected run |
| `x` | Export the visible activity to `artifacts/exports/` as JSON |
| `Ctrl+Q` | Quit |

The activity keys work only on the Activity tab and are hidden from the footer
elsewhere, so evidence has exactly one home. `/threat show [id]` still works
from the chat box, but it answers by switching to Activity and opening the
viewer there. The prompt shown by `p` is
the one that reached the model, after substitution — not the file in
`src/oris/prompts/`. Evidence is matched to a run by the conversation it names
and when it was written, so `e` is only offered on runs that stored some.

Activity needs Phoenix's database, not a running Phoenix: it reads
`~/.oris/traces/phoenix/phoenix.db` directly, read-only. With tracing off the tab
explains that instead of failing, and chat is unaffected. `textual` stays an
optional extra, so an install without it is unchanged.

`langgraph.json` exposes `oris`, `web_research`, and
`community_research` for local LangGraph development-server or Studio use. The
normal CLI and scheduler do not depend on that server. It declares no `env`
file: ORIS reads `~/.oris/.env` itself wherever it is started, so no separate
process needs to be handed the whole file.

## Project documentation

- [Active implementation plan](docs/implementation-plan.md)
- [Implementation history](docs/implementation-history.md)
- [Portable local-first architecture](docs/architecture/001-portable-local-first-foundation.md)
- [Scheduling architecture](docs/architecture/002-project-owned-scheduling.md)
- [Future Web Evidence MCP plan](docs/web-evidence-mcp-plan.md)
