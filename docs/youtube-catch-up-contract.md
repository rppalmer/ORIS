# YouTube Catch-up contract

- Status: Implemented for interactive and scheduled use
- Defined: 2026-08-09

## Purpose

YouTube Catch-up summarizes recent videos from the channel list already
configured in Net-Razor. It is a read-only specialist for followed channels,
not a general YouTube search tool.

The implementation remains a standalone LangGraph specialist and is invoked by
the parent through a wrapper node. The scheduler invokes the same specialist
directly. A dedicated CLI command remains deferred because the generic
scheduled-job command already runs a configured job by ID.

## Official components

- LangGraph `StateGraph` with separate typed input and output schemas owns the
  fixed workflow.
- The existing official `langchain-mcp-adapters` client supplies the approved
  Net-Razor tools over stdio.
- The existing LangChain chat-model integration supplies structured summaries
  through `with_structured_output`.

No new runtime dependency or custom MCP transport is required.

## Capability boundary

The specialist requires exactly three Net-Razor tools:

1. `net_razor_yt_new_videos` discovers a compact queue without transcripts.
2. `net_razor_yt_transcript` fetches one transcript at a time.
3. `net_razor_yt_mark_processed` acknowledges successful transcript call IDs
   after downstream processing completes.

The specialist must not receive `net_razor_yt_channel_digest`. Net-Razor warns
that the bulk tool can return several transcripts in one response and overflow
an MCP host's output limit. It must not receive general YouTube search,
diagnostic, audit-history, X, or Hacker News tools either.

## Public input

The standalone graph accepts only:

- `days`: optional lookback window passed to Net-Razor, whose default and
  validation remain authoritative;
- `max_videos`: total videos processed in one run, default `5`, minimum `1`,
  maximum `10`.

Net-Razor's configured channel list remains the source of truth. The first graph
does not accept arbitrary channels, a topic query, language selection, or
model-controlled tool arguments. `max_videos` is a ORIS orchestration
budget because Net-Razor's existing limit is per channel rather than per graph
run.

## Fixed workflow

1. Validate the ORIS-owned `max_videos` budget before an external call.
2. Call `net_razor_yt_new_videos` exactly once with the configured channels,
   `include_processed=false`, and `days` only when the caller supplied it.
3. Keep only the first `max_videos` entries from Net-Razor's newest-first queue.
4. Process the selected videos sequentially, never in parallel.
5. For each video, call `net_razor_yt_transcript` exactly once with its supplied
   URL and `include_segments=false`. Net-Razor owns language defaults and the
   transcript-length cap.
6. Summarize each successful transcript with one structured model call before
   moving to the next video.
7. Create one final structured digest from the small per-video summaries, not
   from the full transcripts.
8. Validate all final citations.
9. For interactive use, call `net_razor_yt_mark_processed` once with the
   successful transcript call IDs, then return output. Scheduled use follows
   the persistence-first completion sequence defined below.

With the default input, the maximum is seven MCP calls and six model calls: one
discovery call, five transcript calls, one acknowledgement call, five summaries,
and one final digest. With the allowed maximum input, the ceilings are twelve
MCP calls and eleven model calls.

## Evidence and output

Transcript text is untrusted external evidence. It may inform a summary but may
not instruct the model or change the graph's behavior.

The public output contains:

- `answer`: the final catch-up digest;
- `cited_urls`: canonical YouTube URLs used by the final digest;
- `videos`: the successful per-video summaries, with video ID, title, channel,
  publication time, canonical URL, summary, and transcript-truncation status;
- `caveats`: unresolved channels, unavailable transcripts, truncation notices,
  and other handled limitations.

Titles, channels, publication times, video IDs, and URLs come from Net-Razor;
the model does not recreate them. Full transcripts and transcript segments are
not returned in public graph output or added to Local Knowledge. Net-Razor
retains its own audited calls, and Phoenix may contain short-lived development
traces when local tracing is enabled.

Transcript call IDs are retained only in internal graph state until
acknowledgement. They are not included in public graph output or chat history.

Every `cited_urls` entry must exactly match the canonical URL of a successfully
summarized video. A digest with at least one summary requires at least one cited
URL.

## Scheduled job contract

A scheduled YouTube job selects the specialist directly with
`task = "youtube_catch_up"`; it does not use the conversational router. Each job
explicitly supplies `days` and `max_videos` so its operating window and work
budget remain visible in `schedules.toml` and its run history. It does not accept
a natural-language prompt, channel list, language, transcript-length setting,
or MCP tool name. Net-Razor remains authoritative for configured channels,
language selection, and transcript limits.

Each attempted run retains a JSON history record containing:

- job ID and run ID;
- task name, `days`, and `max_videos`;
- start and finish timestamps;
- `running`, `succeeded`, or `failed` status;
- the report path once a report has been written; and
- a short error containing the failed phase when the run does not complete.

The validated Markdown deliverable is written atomically under
`artifacts/scheduled/<job-id>/`. It contains the job ID, run ID, configured
inputs, final digest, one section for each summarized video, cited source links,
and caveats. Each video section includes its title, channel, publication time,
summary, canonical URL, and transcript-truncation status. It never contains a
full transcript, transcript segments, MCP call IDs, or raw MCP responses. A
handled empty queue or a run with no usable transcripts still produces a small
successful report so unattended activity remains visible.

Scheduled completion uses this fixed order:

1. Write the initial `running` history record.
2. Discover, summarize, synthesize, and validate the YouTube result without
   acknowledging any transcript call IDs.
3. Atomically write the Markdown report.
4. Update the `running` history record with the report path.
5. Add the report to Local Knowledge.
6. Acknowledge the successful transcript call IDs through
   `net_razor_yt_mark_processed`. Skip this call when there are no IDs.
7. Mark the run `succeeded` only after acknowledgement succeeds.

A failure before report persistence writes a failed history record and performs
no acknowledgement. A failure during Local Knowledge indexing retains the
report, records its path on the failed run, and performs no acknowledgement. If
acknowledgement fails after the report was saved and indexed, the report is
also retained, the run is marked failed with its report path, and no automatic
retry occurs. The same videos may therefore appear in a later report. This
at-least-once behavior deliberately prefers a possible duplicate report over
marking videos processed without a durable deliverable.

The Markdown file, Local Knowledge database, run-history file, and Net-Razor
database do not share one transaction. Process termination between these steps
can therefore leave a `running` record or repeat work later. Recovery and
automatic reconciliation remain deferred until this is observed as an
operational problem. Net-Razor's acknowledgement remains idempotent, and all
transcript call IDs remain internal orchestration receipts.

## Empty results and failures

- No new videos is a successful empty result with a deterministic message, no
  transcript calls, and no model calls.
- A handled transcript problem, such as disabled captions, skips that video,
  adds a caveat, and continues with the remaining queue.
- If no transcript can be summarized, the graph returns a deterministic
  no-transcripts result without asking the model to fill the gap.
- A malformed MCP response, an MCP execution failure, or a model failure fails
  the run visibly; the first implementation adds no custom retries or fallback
  model.
- Any failure before citation validation prevents acknowledgement, so successful
  transcript fetches remain discoverable on the next run.
- The acknowledgement operation is all-or-nothing and idempotent. A failed
  acknowledgement fails the run; repeating a successful acknowledgement is
  harmless.
- Partial work remains visible in Net-Razor's audit data and Phoenix traces.

Net-Razor stores processed-video state separately from audit history and marks a
video processed only through explicit acknowledgement. Its one-time database
upgrade preserves legacy successful transcript records. Audit pruning does not
erase processed state. ORIS does not maintain a second queue or
duplicate that state.

The shared preparation graph stops after citation validation and returns its
processing receipts only to its caller. The interactive wrapper acknowledges
those receipts immediately before returning public output. The scheduled runner
first writes and indexes the report, then acknowledges the same receipts. Both
paths therefore reuse one research workflow without exposing call IDs to chat,
reports, or Local Knowledge.

## Acceptance checks

Deterministic tests must prove the `max_videos` budget, three-tool allowlist,
exact call arguments, total call ceilings, sequential processing, absence of
transcript segments in requests and public output, exact citation validation,
handled empty results, visible failure behavior, and that acknowledgement cannot
run after synthesis or citation failure. Tests must also prove that ORIS
does not override Net-Razor's provider-owned language, per-channel, or
transcript-length settings.

The implemented scheduler tests additionally prove the persistence-before-
acknowledgement order, exact scheduled inputs, report exclusions, successful
empty results, failures before report creation, and retained reports when
acknowledgement fails.

A separately enabled live evaluation may contact the configured channels and
local model. It must retain its request, output, caveats, source URLs, and call
result for human review. Generated summary quality belongs in that evaluation,
not in blocking word-matching assertions.

## Deferred behavior

- arbitrary YouTube topic search;
- user-supplied channel lists and language preferences;
- parallel transcript or model processing;
- automatic retries or a fallback model;
- multi-worker claims or leases;
- a dedicated CLI command;
- storing full transcripts in ORIS.
