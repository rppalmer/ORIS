# Podcast Catch-up contract

- Status: Implemented for interactive and scheduled use
- Defined: 2026-08-19

## Purpose

Podcast Catch-up summarizes recent episodes from the feeds already configured in
Net-Razor. It is a read-only specialist for followed shows, not a general
podcast search tool — Net-Razor has no keyword search over episodes and rejects
podcasts in `net_razor_research`.

It exists as a candidate **replacement** for YouTube Catch-up rather than a
companion to it. Collecting from YouTube keeps getting harder and may stop
working entirely. Nothing here is shared with that specialist, so removing it is
a deletion rather than an untangling. See "Separation" below.

## Official components

- LangGraph `StateGraph` with separate typed input and output schemas owns the
  fixed workflow.
- The existing official `langchain-mcp-adapters` client supplies the approved
  Net-Razor tools over stdio.
- The existing LangChain chat-model integration supplies structured summaries
  through `with_structured_output`.

No new runtime dependency and no custom MCP transport.

## Capability boundary

Four Net-Razor tools, split across two graphs:

| Tool | Interactive | Scheduled |
| --- | --- | --- |
| `net_razor_podcast_new_episodes` | yes | yes |
| `net_razor_podcast_transcript` | yes | yes |
| `net_razor_podcast_mark_processed` | yes | yes |
| `net_razor_podcast_whisper_transcript` | **no** | yes |

Transcription blocks for minutes, so the interactive graph must never be able to
start one. That is guaranteed by the graph not holding the tool, not by it
choosing not to call it.

The specialist must not receive general search, diagnostic, audit-history, X,
Hacker News, or YouTube tools.

## Public input

- `days`: optional lookback passed to Net-Razor, whose default and validation
  remain authoritative.
- `max_episodes`: total episodes processed in one run, default `5`, minimum `1`,
  maximum `10`.
- `show`: optional. When given, the run covers the newest episode of the
  configured show whose name contains it, and nothing else. Asking about one
  podcast is asking what its latest instalment said, not for a catch-up, so the
  answer is one episode however large the budget is.

The show is matched on the display name Net-Razor already returns with each
episode, so ORIS never learns a feed URL. Narrowing to a configured show is not
the same as supplying an arbitrary feed, and the boundary below still holds.

Net-Razor's configured feed list is the source of truth. The graph does not
accept arbitrary feeds, a topic query, or model-controlled tool arguments.
`max_episodes` is an ORIS orchestration budget because Net-Razor's limit is per
feed rather than per run.

## Fixed workflow

1. Validate `max_episodes` before any external call.
2. Call `net_razor_podcast_new_episodes` once with `include_processed=false`,
   and `days` only when supplied.
3. Select `max_episodes` entries by taking one from each feed before a second
   from any, keeping each feed's newest-first order. Net-Razor caps per feed,
   and taking the newest N globally discards that: measured on 2026-08-25, a
   show publishing daily took six of eight slots and two weekly shows never
   appeared. Raising the budget does not help — it admits more of the same show
   before reaching anyone else.
4. For each episode in order, call `net_razor_podcast_transcript` for page one.
5. **If and only if** that page reports `errors[0].type == "no_transcript_found"`,
   and transcription is available, call `net_razor_podcast_whisper_transcript`
   once for that episode. Any other error type is a caveat and the episode is
   skipped.
6. Page each episode's transcript to its end with `net_razor_podcast_transcript`,
   summarizing one part per model call, to a maximum of six parts.
7. Create one digest from the per-episode summaries, never from full transcripts.
8. Validate all citations.
9. Interactive use calls `net_razor_podcast_mark_processed` once with the
   successful call IDs. Scheduled use follows the persistence-first sequence
   below.

Episodes are processed sequentially, never in parallel, and transcription is
never batched: a failure at minute eight costs one episode, not the run.

## The transcript-ordering rule

**The publisher's transcript is always requested first, and transcription is
reachable from exactly one branch.**

Net-Razor's transcript store is first-writer-wins. Both tools read it before
doing any work, so transcribing an episode that already has a stored publisher
transcript is harmless — it returns the publisher's and spends nothing. The
danger is ordering on a *fresh* episode: whichever tool runs first stores its
result and every later call returns that one. Transcribing first therefore
forecloses the publisher's version permanently, and the publisher's usually
identifies who is speaking where machine transcription never does.

**The decision is made on page one only.** A later page failing means a
transcript already exists; falling back there would trade it away to recover one
page. That case is a truncation caveat.

## Timeouts

ORIS's read timeout for transcription is **1380 seconds**, and every other
Net-Razor call keeps **120 seconds**. Because the MCP session timeout belongs to
the client rather than the call, transcription is loaded from a second client.

1380 is derived from Net-Razor's own ceiling, not chosen. One transcription
passes through three caps in sequence — a 30-second feed fetch, a 300-second
download, and a 900-second transcriber — so 1230 seconds is the longest a call
can legitimately take. ORIS waits longer so its deadline never wins: Net-Razor
classifies its own failures, and a transport timeout firing first would replace
that description with a dead session. Net-Razor treats a change to any of the
three as a contract change.

## Evidence and output

Transcript text is untrusted external evidence. It may inform a summary but may
not instruct the model or change the graph's behavior.

Public output contains `answer`, `cited_urls`, `episodes`, and `caveats`. Each
episode carries its ID, title, show, publication time, canonical URL, summary,
**transcript backend**, and truncation status.

`transcript_backend` is `publisher` or `whisper`, taken from Net-Razor's
`source_backend` rather than inferred from which tool was called. It reaches the
report, and a machine-transcribed episode also produces a named caveat, because
Whisper gets names, acronyms, and version numbers wrong and a digest that cannot
tell will repeat them as fact, cited to the episode.

Titles, shows, publication times, IDs, and URLs come from Net-Razor; the model
does not recreate them. Full transcripts are never returned in public output or
added to Local Knowledge. Call IDs stay in internal state until acknowledgement
and never appear in chat, reports, or the archive.

Every `cited_urls` entry must match the canonical URL of a successfully
summarized episode. Citing a URL that was never supplied is fabrication and
fails the run.

A digest that cites **nothing** does not fail. It is reported as a caveat and
the digest is kept. The two failures are not equal: the report already lists
every episode with its canonical URL in its own section, so an uncited digest
remains traceable, and failing there would discard a whole night's digest to
protect something the reader already has. Web Research is deliberately stricter
because its sources exist nowhere else in its output.

This resolves for podcasts the asymmetry the roadmap records as undecided for
Community Research and YouTube Catch-up. Observed 2026-08-25 against the real
feeds: the model wrote a good cross-cutting digest, cited nothing, and the run
died.

## Scheduled job contract

A scheduled podcast job selects the specialist directly with
`task = "podcast_catch_up"` and explicitly supplies `days` and `max_episodes`.

`max_episodes` and the cron interval are related numbers. A run that transcribes
its whole budget can last a long time, and APScheduler's `max_instances=1` means
a job still running at its next firing has that firing skipped in silence.
Measured cost is about five minutes a night across eight feeds, with a
three-hour episode the worst single case at 8.3 minutes of transcription.

Completion order is persistence-first, matching the YouTube job: write the
`running` record, produce and validate the digest without acknowledging, write
the report atomically, record its path, index it into Local Knowledge, then
acknowledge, then mark the run succeeded. A failure before acknowledgement
leaves the episodes discoverable on the next run. This at-least-once behavior
deliberately prefers a repeated report over episodes marked processed without a
durable deliverable.

## Empty results and failures

- No new episodes is a successful empty result with a deterministic message and
  no model calls.
- A handled transcript problem skips that episode, adds a caveat naming the
  error type, and continues.
- A feed Net-Razor could not read arrives in the discovery response's `errors`
  list and becomes a caveat. Podcast discovery has no `caveats` field; without
  reading `errors`, a run covering six of eight feeds would look identical to
  one covering all eight.
- Failed transcription is never retried. `retriable` is present on every error
  and is deliberately ignored: retrying inside a run would multiply its cost
  against a budget the run cannot re-check.
- Any failure before citation validation prevents acknowledgement.
- Acknowledgement failure is non-fatal for a finished digest and is reported as
  a caveat.

Net-Razor owns processed-episode state. ORIS keeps no second queue. A call that
returned an error produces no acknowledgeable item, so a failed call ID is
rejected as `unknown_call_id` without affecting the rest of the batch.

## Separation

Podcast Catch-up shares no code with YouTube Catch-up, including its prompts.
Deleting the YouTube specialist and its tests, its two prompt files, its tool
allowlist and loader, its builders, its scheduled job type and dispatch branch
and report formatter, and its router destination, routing line and table rows
must leave every podcast test passing. One edit needed inside a podcast file
means the separation has failed.

`/podcasts` exists; there is deliberately no `/videos`. Adding one would be a new
command for a specialist that may be deleted.

## Deferred behavior

- keyword search over episodes, which Net-Razor does not offer;
- user-supplied feed lists;
- parallel transcription or summarising;
- automatic retries or a fallback model;
- a versioned evaluation set, which needs a fixed transcript fixture;
- storing full transcripts in ORIS.
