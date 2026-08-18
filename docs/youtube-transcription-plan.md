# YouTube transcription — the ORIS side

- Status: Planned, not started. Blocked on a Net-Razor measurement.
- Written: 2026-08-18
- Contract it changes: [youtube-catch-up-contract.md](youtube-catch-up-contract.md)

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

## Two gaps in Net-Razor's side

Report these rather than working around them. The first one blocks the design.

### 1. How does ORIS know a video has no captions?

Today's discovery response gives ORIS six fields per video — channel ID, channel
title, video ID, URL, title, published time. None of them says whether captions
exist. ORIS finds out only by calling the transcript tool and getting an error
back.

That leaves two shapes, and only one of them is acceptable:

- **Discovery flags it.** Each entry carries a stable named field meaning "no
  captions" — say `transcript_available: false`. ORIS filters on that field and
  transcribes only those videos. One field, no wasted calls.
- **ORIS infers it from a failed transcript call.** This costs a wasted call per
  caption-less video, and worse, it forces ORIS to tell "captions are disabled"
  apart from "YouTube timed out" and "the video is private" by reading
  Net-Razor's errors. That is Net-Razor's error classification, which ORIS is
  explicitly not allowed to duplicate.

So ORIS needs the discovery field. It must be present whether or not the
transcription flag is on, because ORIS reads it in both cases — with the flag
off it is simply the reason a video is skipped.

### 2. What does the transcription acknowledgement contain?

ORIS needs two things from it, and needs them as data rather than prose:

- Whether a transcript now exists. A status or a boolean, not an error string to
  be pattern-matched.
- When it does not, a short human-readable reason ORIS can copy into a caveat
  verbatim. Net-Razor already records why; ORIS just repeats it.

Anything else in that response — duration, model used, timings — ORIS ignores.

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

Add one node between `discover_videos` and `summarize_videos`. It reads the
discovery entries, keeps the ones flagged as caption-less, and calls the
transcription tool once per video, sequentially, up to the run's budget. Each
call is wrapped so a failure adds a caveat and moves to the next video.

It writes nothing into the summary path. When it finishes, `summarize_videos`
runs exactly as it does now and fetches transcripts for every video in the
queue. A video that transcribed successfully now has one. A video that failed
still does not, and produces the existing "Transcript unavailable" caveat with
no new code. That is the reason this belongs in its own node rather than inside
the summarising loop — it needs no new failure handling at all.

The other reason is the timeout. `SUMMARY_TIMEOUT_SECONDS` is 900 seconds on
`summarize_videos`, and it exists because a hung scheduled run holds its
`max_instances=1` slot and silently kills every later firing. Three
transcriptions at ten minutes each would blow straight through it. Putting
transcription in a separate node lets that node carry its own generous timeout
while the summarising node keeps the 900 seconds it was measured for.

**This was not in the brief and is worth calling out:** the node timeout is a
real ORIS-side change, not just the MCP one. Its value should be derived from
the budget — the number of videos times a per-video ceiling — rather than picked
as a round number, so raising the budget cannot silently outrun it.

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
4 need no changes. That is right for how the transcript is *collected* — it is
an ordinary row in an ordinary table by then.

But ORIS knows which videos it transcribed, because it made the calls. And a
Whisper transcript of a technical talk gets names, acronyms, and product
versions wrong in exactly the places an investigation cares about. The digest
then states those as fact, cited to the video.

Carrying one boolean on each summarised video, and a caveat naming the machine-
transcribed ones, costs almost nothing and keeps the report honest about how
good its evidence is. It does not change how transcripts are fetched or
summarised. Recommended, but it is a call to make deliberately rather than
something to slip in.

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
