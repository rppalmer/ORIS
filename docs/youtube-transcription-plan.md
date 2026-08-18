# YouTube transcription — the ORIS side

- Status: **Parked on a measurement, 2026-08-18.** Not being built. Do not start
  this without re-running the count described under "The gate".
- Written: 2026-08-18
- Contract it would change: [youtube-catch-up-contract.md](youtube-catch-up-contract.md)

## The gate, and why this is parked

The question was how many videos are actually being lost to missing captions.
Net-Razor counted: **four caption failures in its entire audit history**, and
about half of those look like the same video from one channel.

The threshold agreed in advance was that a handful from a single channel means
drop it. That is what the data says, so this is not being built.

One caveat on the number. It came from development traffic, not steady nightly
operation — three active weeks with gaps between them. Re-run the count after a
month of the scheduled catch-up job actually running. If it is still in single
digits, this stays parked permanently.

Everything below is the design, kept because the analysis was done and the
answers from Net-Razor are worth not losing. It is not a work item.

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

## What Net-Razor confirmed

All four were answered on 2026-08-18. Recorded here because they are the parts
that would be expensive to re-derive.

### 1. The transcript error strings are contract

`transcripts_disabled` and `no_transcript_found` come from a mapping table, so
they stay stable even if the upstream transcript library renames its exceptions.
ORIS can branch on them safely.

Also confirmed: keeping caption availability off `yt_new_videos` is right. That
tool builds its queue from channel feeds and never touches a transcript, so a
flag there would force a probe of every video before the queue could return.

### 2. The transcription tool returns an acknowledgement, not text

Failures come back as ordinary `errors` entries, the same shape every other tool
uses. ORIS collects text through `yt_transcript` as it does today.

### 3. The stored-transcript lookup also filters on language code

This is the answer that matters most, and it corrects something this plan
originally got wrong.

`stored_transcript()` does read the `raw` table by `(source='yt',
source_id=video_id)` — but a stored transcript is then **rejected unless its
language code satisfies the request**. A Whisper payload written with a null or
non-standard language code would be invisible to `yt_transcript`, which would go
back to YouTube and fail again with the same caption error.

Silently. The video would look exactly like one that was never transcribed, and
the transcription call would appear to have succeeded. If this is ever built,
this is the integration detail most likely to break it, and the one worth a
deliberate test rather than an assumption.

### 4. Use `source_backend` for provenance, not `is_generated`

Net-Razor agreed that Whisper transcripts should be distinguishable, and that
the original brief was wrong to say otherwise.

The correction: `is_generated` already means "YouTube auto-captions versus
human-uploaded", which is a different question and must not be overloaded. The
new value goes on `source_backend`, today always `"yt-api"`.

### 5. Timing: the ORIS timeout sits above Net-Razor's cap, not below it

There is no measured worst case, because none has been measured.

If this is built, Net-Razor enforces its own transcription cap and returns a
timeout error. So ORIS's read timeout for that tool must sit **above** that cap,
high enough never to fire. The failure then arrives as a normal error response
that ORIS turns into a caveat, rather than as a transport-level timeout it has
to interpret.

That inverts what "Change 1" below was reaching for. The MCP timeout is not the
bound on transcription work; it is a backstop that should never be reached, and
Net-Razor owns the real limit. The LangGraph node timeout is still ORIS's own
concern, because it protects the scheduler slot rather than the call.

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

The long value is chosen to sit above Net-Razor's own transcription cap rather
than to bound the work — see answer 5 above. It should never fire. When
transcription takes too long, Net-Razor returns a timeout error and ORIS treats
it like any other failed video.

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

## The one place the brief was overruled

The brief said nothing should mark a transcript as machine-made. Both projects
now agree that was wrong.

A Whisper transcript of a technical talk gets names, acronyms, and product
versions wrong in exactly the places an investigation cares about. The digest
then states those as fact, cited to the video, with nothing distinguishing them
from a transcript the speaker's own captions produced.

The mechanism is `source_backend`, which every transcript response already
carries and which is always `"yt-api"` today. A different value there lets ORIS
set one boolean on the summarised video and add a caveat naming the
machine-transcribed ones. No new field, and no change to how anything is fetched
or summarised.

Not `is_generated` — that field already answers a different question.

## If this is ever unparked

Do not start from the roadmap. Start by re-running the count in "The gate", then
read "What Net-Razor confirmed", particularly the language-code trap in answer 3.

The build order would be:

1. **Split the interactive and scheduled builders.** Pure refactor, no behaviour
   change. Existing tests pass untouched; add one proving the interactive tool
   set is exactly the three-tool allowlist.
2. **Parameterise the timeout and add the optional loader.** Test that a missing
   transcription tool yields `None` rather than raising, and that the three
   required tools still raise when absent.
3. **Add the budget** to the schedule model and the graph input. Test that it is
   rejected outside its bounds and that zero means no transcription calls.
4. **Add the transcription node.** The only step needing the real tool. Test the
   budget ceiling, one call per video, a failed video becoming a caveat while its
   neighbours proceed, and no transcription calls when the tool is `None`.
5. **Update the contract**, and record the outcome in the implementation history.

Steps 1 to 3 are deterministic and belong in pytest. Whether Whisper transcripts
produce usable summaries belongs in the evaluation set, and is the thing to
actually look at once a real run exists.

Nothing here should be landed early. Two of the three refactors would be
parameters and configuration that nothing reads, which is the speculative
extensibility the simplicity gate rules out.
