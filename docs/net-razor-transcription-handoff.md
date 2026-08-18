# Handoff to Net-Razor: Whisper transcription

- From: ORIS, 2026-08-18
- Full ORIS-side plan: [youtube-transcription-plan.md](youtube-transcription-plan.md)

## What this is

Net-Razor is considering transcribing YouTube videos that have no captions,
using a local Whisper model behind a config flag defaulting to off. ORIS is the
consumer. This is what ORIS needs, what it promises not to do, and the four
questions only Net-Razor can answer.

Nothing is being built on either side yet. The whole thing is gated on one
measurement, described at the end.

## The division of labour

Net-Razor owns the entire audio pipeline: download, Whisper, storage. ORIS
downloads nothing, gains no `yt-dlp` dependency, and knows nothing about audio
formats or YouTube's download surface.

The other split — ORIS fetching audio and handing over a file path — was
considered and rejected. It puts provider-specific retrieval in ORIS, and it
splits one pipeline across two projects so a single failed video has two
possible owners.

## What ORIS will do

Only on the scheduled catch-up job. Never in interactive chat, because nobody
should wait ten minutes on a tool call.

1. Call `yt_new_videos` as it does today.
2. For each video in the queue, call `yt_transcript` once. If it comes back with
   no captions and the run still has transcription budget, call the new
   transcription tool for that one video and move on.
3. Collect transcripts through `yt_transcript` exactly as today — same paging,
   same `call_id` receipts, same `mark_processed` acknowledgement.
4. Summarise as today.

ORIS will call the transcription tool once per video, sequentially, never
batched, with a per-run ceiling it owns. A failure at minute eight costs one
video, not the run.

## What ORIS will not do

- Not duplicate error classification. ORIS reads the `type` field Net-Razor
  publishes; it does not re-derive categories from messages or exception names.
- Not duplicate retry policy. A failed transcription becomes a caveat and the
  run continues. ORIS does not retry.
- Not keep a second queue. `youtube_processed_videos` stays the only record of
  what has been handled.
- Not mirror the new tool's schema. It is discovered at runtime, so the name is
  Net-Razor's to choose and change.
- Not require the tool to exist. With the flag off, ORIS behaves exactly as it
  does today: caption-less videos become caveats.

## Four questions

### 1. Are `transcripts_disabled` and `no_transcript_found` a stable contract?

This is the one that decides the design, and the good news is it needs no code.

ORIS has to know which videos lack captions. Putting a flag on `yt_new_videos`
entries would be wrong — that tool builds its queue from channel feeds and never
touches a transcript, so the flag would force a probe of every video before the
queue could be returned.

It is already answered by `yt_transcript`. A caption failure returns a normal
response whose `errors[0].type` is `transcripts_disabled` or
`no_transcript_found`, distinct from `video_unavailable`, `invalid_video_url`,
and `request_failed`. ORIS branches on that value.

**What ORIS needs:** confirmation that those two strings are a published
contract, not an internal detail that may be renamed. If they are, gap closed
with no change on either side.

### 2. What does the transcription tool return?

ORIS needs exactly two things, both as data rather than prose:

- Whether a transcript now exists. A status or boolean, not a message to
  pattern-match.
- When it does not, a short human-readable reason ORIS can copy into a caveat
  verbatim.

A `ServiceErrorItem` in the usual `errors` list satisfies both and matches every
other tool. Everything else — duration, model size, timings — ORIS ignores.

It should return the acknowledgement, not the text. ORIS collects text through
`yt_transcript`.

### 3. Does the Whisper transcript land where `yt_transcript` already looks?

`stored_transcript()` reads the `raw` table by `(source='yt', source_id=video_id)`
and accepts any payload carrying `segments`. If Whisper output is written in that
shape, ORIS's collection step needs no change at all.

That property is what the entire design rests on, so it is worth confirming
rather than assuming.

### 4. Will the transcript say which backend produced it?

This one is a request rather than a question, and it is the only item that needs
both projects to agree.

Transcript responses already carry `source_backend`, today always `"yt-api"`,
alongside `is_generated`. A Whisper transcript should carry a different
`source_backend` value.

The reason: Whisper mangles names, acronyms, and product versions — exactly the
details an investigation digest then repeats as fact, cited to the video, with
nothing to distinguish it from a transcript the speaker's own captions produced.
One string on Net-Razor's side lets ORIS flag those videos in its report.

ORIS's brief originally said nothing should mark a transcript as machine-made.
This is ORIS arguing the other way, and it is a decision to make deliberately.

## Two operational notes

**Timing.** ORIS expects four to ten minutes for a forty-minute video, blocking.
It will raise its MCP read timeout for this tool alone; the other three keep the
existing 120 seconds. If the realistic worst case is much worse than ten
minutes, say so, because that number sets both the MCP timeout and a LangGraph
node timeout on the ORIS side.

**Progress.** Not required. If the tool happens to emit MCP progress
notifications during a long transcription they will be ignored for now, but they
would make the run easier to diagnose later.

## The gate

Whether any of this is worth building depends on how many videos are actually
being lost.

Net-Razor already records every caption failure. `yt_transcript` logs
`transcript_unavailable video_id=<id> reason=<type>`, and the same handled error
is persisted to the `errors` table against its call, whose `request_json` holds
the video URL. Counting distinct videos with `transcripts_disabled` or
`no_transcript_found` over the last few months is one query.

If the answer is a handful, this is not worth a Whisper pipeline. If it is a
steady fraction of the weekly queue, it is.

ORIS is not building anything until that number exists.
