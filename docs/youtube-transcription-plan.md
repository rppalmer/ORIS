# YouTube transcription — the ORIS side

- Status: Planned, not started. Blocked on a Net-Razor measurement.
- Written: 2026-08-18
- Contract it changes: [youtube-catch-up-contract.md](youtube-catch-up-contract.md)
- Handoff for the Net-Razor side: [net-razor-transcription-handoff.md](net-razor-transcription-handoff.md)

## What is changing

Net-Razor is gaining the ability to transcribe a YouTube video that has no
captions, using a local Whisper model. Today those videos are lost: Net-Razor
records the caption failure, ORIS turns it into a caveat, and the video is never
summarised.

Net-Razor owns the whole audio pipeline. It downloads the audio, runs Whisper,
and stores the transcript. ORIS downloads nothing, gains no `yt-dlp`
dependency, and learns nothing about audio formats or YouTube's download
surface.

The other split — ORIS fetching the audio and handing Net-Razor a file path —
was considered and rejected. It moves provider-specific retrieval into ORIS,
which the MCP capability rules forbid, and it splits one pipeline across two
projects so a single failed video has two possible owners.

## The flow

The scheduled catch-up job, mostly as it already runs:

1. Call `net_razor_yt_new_videos` as today. Some entries come back with no
   transcript available.
2. For each of those, call a new Net-Razor tool that transcribes one video. It
   blocks. Expect four to ten minutes for a forty-minute video. It stores the
   transcript and returns a small acknowledgement, not the text.
3. Collect the text through `net_razor_yt_transcript`, exactly as today. Same
   paging, same receipts.
4. Summarise as today.

Steps 3 and 4 need no code changes. That is the point of the design: once the
transcript exists in Net-Razor's store, it is an ordinary transcript.

## Constraints

These are project-wide and apply to every task below.

- The new tool is discovered at runtime. Its name is not settled. Its schema is
  not mirrored in ORIS.
- ORIS does not duplicate Net-Razor's error classification, retry behaviour, or
  processed-video state. A video that fails to transcribe gets reported and
  skipped.
- One video per call. No batching. A failure at minute eight costs one video,
  not the run.
- The interactive graph never receives the transcription tool. Nobody waits ten
  minutes on a tool call in chat.
- Transcription sits behind a Net-Razor config flag that defaults to off, so the
  tool may be absent entirely. ORIS must behave exactly as it does today when it
  is.
- No new runtime dependency. Verified: nothing below needs one.

## What ORIS needs from Net-Razor

One of these is a confirmation rather than new work. The other two are real.

### 1. A stable contract for "this video has no captions"

Discovery cannot answer this and should not be made to. `yt_new_videos` builds
its queue from channel feeds and never touches a transcript, so putting an
availability flag on a discovery entry would mean probing every video before
returning the queue.

It is already answered somewhere better. When `yt_transcript` cannot get
captions it returns an ordinary response whose `errors[0].type` is
`transcripts_disabled` or `no_transcript_found` — distinct from
`video_unavailable`, `invalid_video_url`, and `request_failed`. ORIS reads that
field and branches on it.

That is consuming Net-Razor's error classification, not duplicating it.
Duplicating would be ORIS re-deriving the category from exception types or from
message text. What ORIS needs is only the promise that those two values are a
published contract rather than an internal detail free to be renamed.

So: no change, just confirmation.

### 2. What the transcription tool returns

ORIS needs two things from the acknowledgement, both as data rather than prose:

- Whether a transcript now exists. A status or a boolean, not an error string to
  be pattern-matched.
- When it does not, a short human-readable reason ORIS can copy into a caveat
  verbatim. Net-Razor already records why; ORIS only repeats it.

A `ServiceErrorItem` in the existing `errors` list would satisfy both, and would
match every other tool's shape.

Anything else in the response — duration, model size, timings — ORIS ignores.

### 3. Where the Whisper transcript gets stored

`yt_transcript` already serves a repeat or paged call from `stored_transcript()`,
which reads the `raw` table by `(source='yt', source_id=video_id)` and accepts
any payload carrying `segments`. If Whisper output is written in that same shape,
ORIS's collection step works with no change whatsoever — which is the property
the whole design depends on.

Worth confirming explicitly, because it is the difference between "steps 3 and 4
need no changes" being true and being nearly true.

### 4. One request: say which backend produced the transcript

The transcript response already carries `source_backend`, currently always
`"yt-api"`, and `is_generated`, which distinguishes YouTube's own auto-captions
from human ones. A Whisper transcript should carry a different `source_backend`
value.

The reason is in the last section of this plan. It costs one string.

## What changes in ORIS

Five changes. Four are small; one is structural.

### The structural one: the scheduled and interactive paths are the same object

`build_youtube_catch_up_preparation` in [web_research_app.py](../src/oris/web_research_app.py)
builds the graph. The scheduler imports that function directly, and the
interactive builder wraps the very same call. There is currently no such thing
as "the scheduled variant" — there is one builder with two wrappers.

Every other requirement here depends on separating them: the tool allowlist
differs, the MCP timeout differs, and the node timeout differs. So the split
comes first and everything else follows from it.

### Change 1: make the MCP read timeout a parameter

`create_net_razor_client` in [net_razor.py](../src/oris/net_razor.py) hard-codes
a 120-second session read timeout.

Two facts worth knowing before touching it, both read from the installed
packages rather than remembered:

- The client is stateless, so `langchain_mcp_adapters` opens a fresh session for
  every single tool call. A session-level timeout is therefore already a
  per-call timeout in practice.
- `mcp.ClientSession.call_tool` does accept a per-call `read_timeout_seconds`,
  but the adapter never passes it through. So a per-call override is not
  reachable without reimplementing the tool wrapper, which is out.

That leaves one clean route: give `create_net_razor_client` a timeout argument
and build more than one client.

The scheduled builder should build two. Discovery, transcript, and
acknowledgement keep the existing 120 seconds. The transcription tool alone gets
the long one. The alternative — one client with a long timeout for everything on
the scheduled path — is simpler by one line and gives up something real: a
transcript fetch that hangs on YouTube would sit there for fifteen minutes
instead of two, and could exhaust the node timeout that protects the rest of the
run. Two clients cost nothing at rest, because the stdio process spawns per call
either way.

Interactive keeps one client at 120 seconds and never sees the second.

### Change 2: load the new tool separately, and tolerate its absence

`_load_tools` raises if any name in its allowlist is missing. That is correct for
the three required tools and wrong for this one, which is absent whenever
Net-Razor's flag is off.

So the transcription tool gets its own loader that returns the tool or `None`.
Absent is not an error. The scheduled builder passes whatever it got into the
graph, and a `None` there means the graph behaves precisely as it does today.

`YOUTUBE_CATCH_UP_TOOL_NAMES` stays a three-tool allowlist. The new name lives
beside it as an optional fourth, and the ordering check in
`create_youtube_catch_up_preparation_graph` is untouched.

### Change 3: a new graph node that transcribes, with its own budget

Add one node between `discover_videos` and `summarize_videos`. For each
discovered video, in queue order, it calls `net_razor_yt_transcript` once and
looks at the result:

- Text came back. Nothing to do. The fetch is now in Net-Razor's store, so the
  summarising node's own fetch is served locally.
- The error type is `transcripts_disabled` or `no_transcript_found`, and budget
  remains. Call the transcription tool for that video, then move on.
- Anything else, or the budget is spent. Add a caveat and move on.

It writes nothing into the summary path. When it finishes, `summarize_videos`
runs exactly as it does now. A video that transcribed successfully has a
transcript. One that failed still does not, and produces the existing
"Transcript unavailable" caveat with no new code. That is why this belongs in
its own node rather than inside the summarising loop: it needs no new failure
handling at all.

The cost is one extra MCP round-trip per video. It is smaller than it looks.
For a video with captions, this node performs the fetch that used to happen in
the summarising node, and the later one is served from local storage — so the
number of calls that leave the machine is unchanged. For a caption-less video,
this is the call that discovers the problem, which is work either way.

The other reason for a separate node is the timeout. `SUMMARY_TIMEOUT_SECONDS`
is 900 seconds on `summarize_videos`, and it exists because a hung scheduled run
holds its `max_instances=1` slot and silently kills every later firing. Three
transcriptions at ten minutes each would go straight through it. A separate node
carries its own generous bound while the summarising node keeps the 900 seconds
it was measured for.

**This was not in the brief and is worth calling out:** the node timeout is a
real ORIS-side change, not just the MCP one. Its value should be derived from
the budget — videos times a per-video ceiling — rather than picked as a round
number, so raising the budget cannot silently outrun it.

### Change 4: the per-run transcription budget

Net-Razor's limits are per channel. How much Whisper time one scheduled run may
spend is an orchestration cost ceiling that Net-Razor cannot know, which is the
same argument already recorded for `max_videos`.

It goes in two places:

- `YouTubeCatchUpScheduledJob` in [schedules.py](../src/oris/schedules.py), next
  to `days` and `max_videos`, so the cost is visible in `schedules.toml` and in
  every run-history record. Make it required like its neighbours — the contract
  says each job states its work budget explicitly, and there is no YouTube job
  configured today, so there is nothing to migrate. Zero is a legal value and is
  what you set while Net-Razor's flag is off.
- `YouTubeCatchUpInput` on the graph, defaulting to zero. Interactive passes
  nothing and therefore gets zero, which is the second lock on the interactive
  path after simply not holding the tool.

Videos left untranscribed because the budget ran out get a caveat saying so.
They stay unacknowledged, so they return in the next run's queue.

### Change 5: update the contract document

[youtube-catch-up-contract.md](youtube-catch-up-contract.md) needs the fourth
tool as an optional scheduled-only capability, the new budget in the public
input and the scheduled job contract, the revised call ceilings, the new node in
the fixed workflow, and one line in the failure section saying a failed
transcription is a caveat rather than a run failure.

It also needs a line in the scheduled section noting that a run can now last
tens of minutes, so a job whose cron fires more often than its worst case will
have firings skipped by `max_instances=1`. For a daily job this is harmless. It
should be written down rather than discovered.

## One recommendation the brief argues against

The brief says nothing marks a transcript as machine-made, and that steps 3 and
4 need no changes. That is right for how the transcript is *collected* — by then
it is an ordinary row in an ordinary store.

But a Whisper transcript of a technical talk gets names, acronyms, and product
versions wrong in exactly the places an investigation cares about. The digest
then states those as fact, cited to the video, with nothing distinguishing them
from a transcript the speaker's own captions produced.

The mechanism already exists. Every transcript response carries `source_backend`,
today always `"yt-api"`. A Whisper transcript carrying a different value means
ORIS can set one boolean on the summarised video and add a caveat naming the
machine-transcribed ones. No new field, no change to how anything is fetched or
summarised.

Recommended. It is a call to make deliberately rather than something to slip in,
and it is the one item here that needs both projects to agree.

## Sequencing

Nothing here gets built yet.

The Net-Razor tool does not exist, and whether it gets built is gated on a
measurement: how many videos are actually being lost to missing captions.
Net-Razor already records every caption failure with its video ID, so that is
one query.

It is tempting to land the refactors now — the timeout parameter, the builder
split, the budget field — since none of them depend on the new tool's schema.
Do not. Two of the three would be configuration and parameters that nothing
reads, which is the speculative extensibility the simplicity gate rules out.
They are cheap to do later and the plan is written down here.

When the gate clears, build in this order. Each step leaves the tree working and
tested.

1. **Split the builders.** Pure refactor, no behaviour change. Existing tests
   must pass untouched; add one proving the interactive tool set is exactly the
   three-tool allowlist.
2. **Parameterise the timeout and add the optional loader.** Test that a missing
   transcription tool yields `None` rather than raising, and that the three
   required tools still raise when absent.
3. **Add the budget** to the schedule model and the graph input. Test that it is
   rejected outside its bounds and that zero means no transcription calls.
4. **Add the transcription node.** This is the one that needs the real tool.
   Test the budget ceiling, one call per video, a failed video becoming a caveat
   while its neighbours proceed, and no transcription calls at all when the tool
   is `None`.
5. **Update the contract**, and record the outcome in the implementation
   history.

Steps 1 to 3 are deterministic and belong in pytest. Whether Whisper transcripts
produce usable summaries is a question for the evaluation set, not a blocking
assertion — and it is the thing to actually look at once a real run exists.
