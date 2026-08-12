# ADR 001: Portable, local-first agent foundation

- Status: Accepted
- Date: 2026-07-20
- Last reviewed: 2026-08-10

## Context

The project is a personal, stateful agent with command-line chat, Web Research,
Community Research, local knowledge recall, and specialist LangGraph workflows.
Scheduled work is implemented and additional specialists are planned.
Development happens on a MacBook. An always-on M4 Mac mini with 32 GB of unified
memory runs oMLX and a configured local instruction model. The finished
application is expected to run on the Mac mini.

The system must favor predictable control flow, local data handling,
traceability, and a design that teaches practices transferable to larger
systems. The first capability is read-only web research using Tavily.

## Decision

### Portable application boundary

The Python application will communicate with the model through oMLX's
OpenAI-compatible HTTP API. Application code will not import MLX libraries or
depend on Apple-specific filesystem paths.

Machine-specific values will be provided through validated environment
configuration, including:

- model base URL, model identifier, and local API credential;
- Tavily API credential;
- Phoenix collector endpoint;
- storage locations and runtime settings.

Moving the application from the MacBook to the Mac mini must require
configuration changes, not source-code changes. On the MacBook, the model URL
will refer to the Mac mini over the private network. On the Mac mini, it can
refer to localhost.

### Development topology

During development:

- the MacBook runs the source code, LangGraph development server, Studio
  browser session, and Phoenix;
- the Mac mini runs oMLX and the configured model;
- Tavily receives only the search requests required by the graph;
- LangSmith tracing remains disabled;
- no public tunnel is used.

The oMLX endpoint should require an API key even though it is reachable only on
the private network.

### Model usage

The model name is runtime configuration, not an architectural dependency.
ORIS may change models without changing graph or application code as
long as the replacement is served through the configured OpenAI-compatible
endpoint and passes focused compatibility contracts for the behavior it will
use, such as context length, structured output, tool calling, streaming, and
timeouts. Model-specific results belong in evaluation artifacts and
implementation history.

ORIS relies on oMLX's request batching and does not globally serialize
model calls. Additional application-level concurrency controls will be added
only if observed failures demonstrate a need.

The parent graph uses one structured model call to select exactly one of five
fixed destinations for ordinary CLI input: direct chat, Web Research, Community
Research, Local Knowledge, or YouTube Catch-up. The same structured result
resolves a contextual follow-up and prepares the selected specialist's input,
avoiding a second model call. Community Research receives only a concise topic;
the other specialists receive a standalone request. Explicit slash commands
bypass that model call and use their supplied request unchanged. The router cannot choose
tools, invent graph names, or alter specialist bounds; a routing failure closes
the request rather than guessing a destination.

Conversation history sent to the model is bounded by a configured token budget
using LangChain's `trim_messages`, keeping the most recent turns and always
retaining the current request even when it alone exceeds the budget. Trimming
applies to the prompt only; the checkpointed thread keeps its full history.
Exceeding the context window is otherwise unrecoverable for a thread, because
every later turn in it fails identically.

Application failures are state, not conversation. A node error records the
failed component and reason in a non-message state field and removes the failed
user turn with LangGraph's standard message operation. The CLI displays that
error, while future model calls and Local Knowledge receive neither the failed
input nor a synthetic assistant error message.

### Web Research graph

Web Research is a fixed workflow, not an orchestrator:

1. validate the request;
2. use one bounded structured model call to create a search request while
   preserving the original question for the final answer;
3. perform exactly one basic Tavily search;
4. normalize the provider response into typed evidence records at the adapter
   boundary;
5. synthesize an answer with citations; and
6. validate that every citation identifies supplied evidence.

Tavily parameters remain bounded and conservative. Basic search is used with
automatic provider parameters disabled. Explicit callers may provide bounded
domain or recency controls; otherwise the planner may add them only under its
documented rules. The planner selects the existing `news` category only for an
explicit news request; an explicit caller category takes precedence. Provider
date filters represent source-publication recency, not the date of a current
condition. Current or historical state such as weather, prices, scores, and
service status keeps its resolved date in the search query without requiring
page publication metadata. Explicit scheduled news continues to use strict
absolute publication bounds. Tavily's generated answer and raw page-content
options remain disabled. Firecrawl remains deferred until a demonstrated need
exists for full-page extraction.

### External capability boundary

Graphs and graph state depend on application-owned request and result schemas
rather than provider SDK types. An external capability may later be supplied by
a direct API adapter, an MCP server, or another local service without changing
the parent graph's contract. A specialist may also retain an MCP server's
compact normalized JSON as evidence when that artifact is already the approved
capability boundary; raw upstream provider responses do not enter graph state.

MCP standardizes discovery, transport, and tool invocation. It does not make
unrelated tool names, input schemas, output shapes, or processing guarantees
interchangeable. ORIS therefore uses fixed specialist workflows for repeatable
work and treats provider replacement as an adapter concern. A replacement
provider must satisfy the same ORIS capability semantics; ORIS will not add a
second queue, silently drop unsupported controls, or otherwise compensate for
missing provider behavior.

The current Tavily integration already implements ORIS's `WebSearch` boundary
and normalizes Tavily results before they enter Web Research. Tavily-specific
request mapping remains in that adapter. A second web-search backend will add
its own adapter only when it exists; provider-selection configuration will not
be added in advance.

### MCP independence

ORIS is a foundational orchestrator that MCP servers augment. No MCP server is
part of the core. This is a structural constraint, not an aspiration:

- The application must start, and every capability that does not need a given
  MCP server must work, when that server is absent, misconfigured, or failing.
  Direct chat, Web Research, and Local Knowledge therefore never depend on
  Net-Razor being installed or reachable.
- MCP-backed specialists resolve their connection on first use, not at startup.
  An unavailable server degrades exactly one capability and reports through the
  normal node-failure path; it does not prevent the process from running.
- No MCP server name, interpreter path, or tool name may be required
  configuration for the application to load.

Provider-specific tool names, arguments, and result mapping belong in
ORIS-owned adapters. Where they are currently inline in a specialist, that is a
known deviation to be repaid when the specialist is next changed — it is not
accepted behavior and must not be cited as precedent.

The current Community Research and YouTube Catch-up implementations still read
Net-Razor's exact tool names and structured JSON inline. ORIS connects to
Net-Razor over stdio with the official `langchain-mcp-adapters` package and a
machine-configured absolute interpreter path, and that path is optional
configuration. Their fixed tool allowlists and persistence-before-acknowledgement
behavior are correct and stay. A replacement server's tool calls and result
mapping move behind ORIS-owned Community Research or YouTube capability adapters
without changing the specialist workflow. A server without equivalent
processed-video acknowledgement is not a YouTube Catch-up replacement.

The Threat Intel specialist connects to ThreatSyft's two stdio MCP servers with
a four-tool allowlist: `extract_iocs`, `enrich`, `lookup`, and `search`. Its
workflow is fixed and defensive — ThreatSyft extracts the indicators, ORIS
enriches at most a bounded number of them per run, and the answer may only cite
evidence keys the servers actually returned. Indicator extraction, provider
selection, and result normalisation stay in ThreatSyft; the per-run indicator
cap is an ORIS-owned orchestration budget because the MCP contract cannot
express a limit across separate calls.

The complete provider responses behind a report are written to
`artifacts/threat/` rather than returned, because they are several times larger
than the pivot and would cost context on every later turn. Everything needed to
find, identify, or age out a report is encoded in its filename, so there is no
index that can fall out of step with the files. `/threat show` reads them
without touching the graph, which is what keeps a large report free: it reaches
the terminal without entering the conversation, the checkpoint database, or any
later prompt. Retention is a fixed age window swept whenever a report is
written, read from the filename rather than the file's mtime, which a backup or
sync client would rewrite.

`/threat report` returns the collected evidence instead of a written answer,
pivoted from source-major to field-major so the providers that answered the same
question sit together and disagree visibly. It is a reorganisation, not a
judgement: every value keeps the name of the source that produced it, and no
verdict is computed. That path makes no model call at all, so nothing is lost to
summarising. Lists of objects are replaced by their counts, which is what keeps
a ten-source report about a fifth of the size of the raw fan-out.

Within Threat Intel, `/threat enrich` and `/threat ref` name the capability
deterministically and make no planning model call at all. A freeform `/threat`
uses one constrained structured-output call that selects a capability from a
closed set; it is a plan ORIS then executes, not a model-controlled tool loop.
The planner cannot name indicators — those come only from ThreatSyft's
deterministic extraction over the user's own text, so no model decision can
cause egress of a value the user never supplied.

Threat Intel is reachable only through the explicit `/threat` command and is
absent from the router's structured-output schema, so a model decision cannot
select it. `enrich` sends indicators to third-party providers and consumes paid
credits; that egress remains a deliberate user action, not an inferred one.

An optional dynamic MCP exploration agent is deferred. If later approved, it
will be a separate interactive, read-only specialist with a small explicit tool
allowlist and bounded execution. It will not replace fixed specialists, run
scheduled jobs, or perform persistence-sensitive acknowledgement workflows.

### Determinism

The project targets deterministic control and reproducible diagnosis, not
byte-identical prose. Graph routes, tool permissions, request parameters,
budgets, retries, timeouts, prompt versions, and configuration will be
explicit. Live web responses and generated wording may change.

### Observability and retention

Phoenix will run locally on the MacBook as an optional development process. It
will use SQLite, disable its analytics and unneeded services, and retain traces
for 14 days. LangGraph Studio will connect to the local development server with
LangSmith tracing disabled and CLI analytics disabled.

The direct CLI uses the official asynchronous SQLite checkpointer for durable
conversation threads. The raw Tavily response will not be written into graph
state; the adapter will convert it to the minimum typed evidence required by
the workflow. The parent conversation checkpoint retains the user-facing
messages and request status, not the provider's raw response. Provider inputs
and outputs may appear in the short-lived diagnostic trace. Scheduled
collection tasks explicitly persist their final output artifacts under their
own retention policy.

This separates four concerns:

- checkpoints support workflow recovery and thread continuity;
- traces support diagnosis and performance analysis;
- retained research artifacts exist only when they are a product requirement;
- a separate knowledge index, rather than raw checkpoints or traces, supports
  search across completed chat turns and scheduled reports.

The SQLite FTS5 knowledge index stores successful CLI exchanges and scheduled
reports and supports explicit `/recall` across both sources. Local Knowledge
uses one constrained model call to convert the natural-language question into
concise search terms, a source filter, and relevance-or-newest ordering before
SQLite performs the search. A newest plan returns one document; relevance
returns up to five. Recall answers are not added back to the index because they
are derived copies of existing evidence.

### Side effects

The system may read remote data and write local state, traces, and
configuration-derived artifacts. It may not post, message, delete, purchase,
or modify remote data. Human approval is therefore not part of the current
read-only workflows.

## Consequences

- The same application package can run on either Mac.
- Development observability consumes resources on the MacBook, not the model
  host.
- Phoenix placement for always-on scheduled execution remains a later decision
  and will be based on measured CPU, memory, and disk usage.
- Replaying an old web answer after trace/checkpoint expiry will not be possible
  unless that task explicitly saved an artifact.
- The command-line chat interface and local knowledge index are implemented.
- The local in-memory scheduler runtime and transitional LaunchAgent management
  tooling are implemented. The LaunchAgent is installed, its lifecycle and one
  unattended Web Research job have been verified. Scheduled YouTube Catch-up
  also has validated configuration, execution, report retention, Local
  Knowledge ingestion, and persistence-before-acknowledgement behavior. Only
  reboot/login recovery remains for the transitional service. A general
  model-controlled tool loop, Firecrawl, and a graphical chat UI remain
  deferred.
- The official Net-Razor stdio boundary and Community Research specialist are
  implemented. The accepted YouTube Catch-up specialist is connected through a
  wrapper node because its state schema differs from the parent chat schema.
  The constrained parent router can select either specialist without
  introducing a general model-controlled tool loop.
- MCP-backed specialists are resolved lazily on first use, so ORIS starts and
  serves direct chat, Web Research, and Local Knowledge with Net-Razor absent.
  A missing or failing server surfaces as a normal per-request failure naming
  the specialist, not as a startup crash.
- Fixed workflows and dynamic agents are both valid LangGraph patterns. ORIS
  uses fixed specialists for repeatable work and reserves any future dynamic MCP
  agent for bounded, ad-hoc exploration.

## References

- [LangGraph application structure](https://docs.langchain.com/oss/python/langgraph/application-structure)
- [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph data storage and privacy](https://docs.langchain.com/langsmith/data-storage-and-privacy)
- [Phoenix configuration](https://arize.com/docs/phoenix/self-hosting/configuration)
- [oMLX API and configuration](https://github.com/jundot/omlx/blob/main/README.md)
