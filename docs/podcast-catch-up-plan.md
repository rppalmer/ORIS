# Podcast Catch-up — the ORIS side

- Status: Planned, not started. Awaiting approval to build.
- Written: 2026-08-18
- Net-Razor: podcast source merged on `main` (`55ee11e`)
- Sibling contract: [youtube-catch-up-contract.md](youtube-catch-up-contract.md)

## Why this exists

YouTube audio can no longer be downloaded — proof-of-origin tokens, scrambled
URLs, and a streaming model that exposes no file. Podcast RSS has none of that:
a plain GET of a URL the publisher advertises. Spoken content now arrives
through podcasts, and eight feeds are configured.

YouTube Catch-up stays exactly as it is. Nothing in this plan touches it.

This also kills the parked YouTube Whisper work outright rather than leaving it
waiting for a re-count. See the last section.

## What I verified against Net-Razor

Read from the merged source, not assumed:

- All four tools exist with the stated signatures.
- `podcast_whisper_transcript` is registered **unconditionally**. With the
  feature off it returns a `not_configured` error rather than disappearing. So
  it is a required tool, not an optional one — simpler than expected.
- The discovery item mapping is exactly as briefed: `source_id` is the episode
  ID, `query_used` is the feed URL, `author.display_name` is the show. The feed
  URL is also on `author.handle`, described in Net-Razor as "its stable handle".
  Either works; `query_used` is what the brief names, so use that.
- Error types are `no_transcript_found`, `not_configured`, `whisper_unavailable`,
  and `transcription_timeout`, each carrying `retriable`.
- Both transcript tools return `call_id` at the top level, which is the receipt
  `podcast_mark_processed` takes.

## One gap: ORIS's read timeout has to clear two of Net-Razor's, not one

The brief says raise ORIS's read timeout to about 15 minutes. That is below the
worst case Net-Razor can legitimately take, because one Whisper call has **two**
independent timeouts and they run in sequence:

- `podcast_audio_timeout_seconds` — **300s**, the audio download.
- `podcast_whisper_timeout_seconds` — **900s**, wrapping only the transcriber
  subprocess. The download is not inside it.

So one call can legitimately run for **1200 seconds — twenty minutes** — plus
subprocess start and MCP framing, before Net-Razor itself gives up.

At a 15-minute read timeout ORIS would abandon calls Net-Razor is still working
on correctly. Worse than slow: the run gets a dead MCP session instead of a
classified `transcription_timeout` or download error carrying `retriable` and a
message worth putting in a caveat. Net-Razor did the work of classifying that
failure and ORIS would throw it away.

**ORIS should use 1380 seconds (23 minutes)**, clearing 1200 with three minutes
of margin. The principle is the one Net-Razor gave during the YouTube Whisper
review: their caps are the real limits, and ORIS's timeout is a backstop that
should never fire.

An earlier draft of this plan said 1080 seconds. That was wrong — it cleared the
transcription cap but not the download sitting in front of it.

**What to confirm:** that 300 and 900 are the values actually running here. Both
are configurable, and if either moves, ORIS's 1380 moves with it. This is the
one number two projects have to keep in step.

## The flow

Scheduled path only, for the Whisper half.

1. `podcast_new_episodes` — a queue of episodes carrying descriptions, no
   transcripts.
2. For each episode within budget, `podcast_transcript` first. Immediate, and
   when a show publishes its own it usually identifies who is speaking.
3. Only if that returns `errors[0].type == "no_transcript_found"`, call
   `podcast_whisper_transcript`. Roughly one minute per twenty minutes of audio.
4. Summarise per page, as YouTube already does.
5. `podcast_mark_processed` with the `call_id` from whichever transcript call
   succeeded.

Roughly a quarter of feeds publish transcripts, so Whisper is the normal path
rather than the exception.

## What changes in ORIS

Six pieces. One is a design trap, the rest are mechanical.

### 1. The ordering rule has to be structural, not documented

Calling Whisper on an episode that already returned a publisher transcript
**overwrites it**, and the published one usually has speaker labels while
Whisper never does. Net-Razor deliberately has no guard. It is the consumer's
job.

So the Whisper call must be reachable from exactly one place: the branch where
the first page came back with `no_transcript_found`. Not on success. Not on any
other error type. Not on a later page.

That last one is the trap. If page one succeeds and page three fails, ORIS
already holds a publisher transcript, and falling back to Whisper there would
destroy it to recover one page. **The Whisper decision is made once, on the
first page only.** A later page failing is a truncation caveat, exactly as
YouTube handles it today.

### 2. Paging after a Whisper transcript uses the ordinary transcript tool

Net-Razor's own documentation says that once an episode is transcribed,
`podcast_transcript` returns that transcript thereafter and re-asking is cheap
because it is served from storage. So after a successful Whisper call, page on
with `podcast_transcript`, not with Whisper.

Provenance survives: `podcast_transcript` serving a stored Whisper transcript
reports `source_backend` from the stored payload, so it still says `whisper`.

This is worth being deliberate about. Paging with the Whisper tool would at best
be pointless and at worst re-enter the expensive path once per page.

### 3. Two MCP clients, because the timeout differs per tool

`create_net_razor_client` hard-codes a 120-second read timeout for every tool it
serves. Two facts, read from the installed packages:

- The client is stateless, so the adapter opens a fresh session per tool call. A
  session timeout is already a per-call timeout in practice.
- `ClientSession.call_tool` accepts a per-call timeout, but the adapter never
  passes it. There is no per-call override without reimplementing the wrapper.

So `create_net_razor_client` gains a timeout argument, and the scheduled podcast
builder constructs two clients: 120 seconds for discovery, transcript, and
acknowledgement, and 1080 seconds for the Whisper tool alone. Two clients cost
nothing at rest because the subprocess spawns per call either way.

Sharing one long timeout across all four would mean a hung feed fetch sitting
for eighteen minutes instead of two, inside a node whose whole job is to finish
before the next scheduled firing.

### 4. Separate interactive and scheduled builders, from the start

`build_youtube_catch_up_preparation` is currently shared: the scheduler imports
it directly and the interactive builder wraps the same call. There is no
"scheduled variant" of anything today.

Podcast is new code, so it gets built correctly rather than refactored later:

- **Interactive** — `new_episodes`, `transcript`, `mark_processed`. One client at
  120 seconds. Never holds the Whisper tool.
- **Scheduled** — the same three, plus `whisper_transcript` from the long client.

Not holding the tool is the real guarantee. The budget defaulting to zero is a
second lock, not the primary one.

### 5. A new specialist module, its own graph nodes

Same shape as YouTube: discover, transcribe, summarise per page, digest,
validate citations, acknowledge. The transcription work belongs in **its own
node**, not inside the summarising loop, for the same reason it did in the
YouTube plan: it needs a generous timeout of its own while the summarising node
keeps a tight one.

Two things differ from YouTube's output:

- **`transcript_backend` on every summarised episode**, carrying `publisher` or
  `whisper` straight from `source_backend`. It goes in the Markdown report per
  episode, and the episodes transcribed by Whisper get a named caveat. This is
  not decoration: Whisper mangles names, acronyms, and version numbers, which is
  precisely what a digest repeats as fact, cited to the episode.
- **Show name comes from `author.display_name`**, where YouTube uses
  `channel_title`.

### 6. The per-run episode budget, and the arithmetic that follows

An ORIS-owned ceiling like `max_videos`, because Net-Razor caps per feed and
cannot know what one run can afford. It goes in the graph input and in a new
`PodcastCatchUpScheduledJob` beside `days`, so the cost is visible in
`schedules.toml` and in every run record.

The node timeout must be **derived from that budget**, not picked as a round
number, so raising the budget cannot silently outrun it. Worst case is 8.3
minutes for the longest configured show, so a budget of five implies roughly 42
minutes of transcription plus fetching and summarising.

That has a consequence worth writing into the contract: scheduled jobs use
`max_instances=1` with `coalesce=True`, so a run still going at the next firing
causes that firing to be skipped in silence. Measured cost is about five minutes
a night, so this is comfortable — but the budget and the cron interval are now
related numbers, and nothing in the code says so.

## The question I cannot answer alone

**How does the router tell "catch up on podcasts" from "catch up on videos"?**

YouTube Catch-up has no slash command. It is router-only, selected by a single
line in the routing prompt about catching up on configured channels. Adding a
second catch-up destination makes a bare "catch me up" genuinely ambiguous, and
the router picks exactly one destination.

Three options:

1. **Routing lines only.** Add a podcast line and rely on the user saying
   "podcasts" or "videos". Cheapest. Leaves the bare phrasing a coin flip.
2. **Explicit slash commands** for both, keeping the routing lines. Follows the
   `/research`, `/community`, `/recall` precedent, and gives a deterministic
   override where the router is genuinely uncertain. My recommendation.
3. **One catch-up destination covering both.** Rejected — it doubles the work
   and the cost of every casual request, and the scheduled paths need to stay
   separately budgeted anyway.

I would take option 2, but adding a slash command changes the advertised command
set, which is a user-facing decision rather than an implementation detail.

## Deliberate duplication, stated so it is a choice

Podcast Catch-up will duplicate most of YouTube Catch-up: the paging loop, the
per-page summarising, the digest and citation validation, the acknowledgement
wrapper, the scheduled job runner, and the report formatter. The differences are
field names, one extra tool, one extra output field, and the timeout.

Extracting a shared spoken-content specialist now would mean changing YouTube
Catch-up, which the brief rules out, and would mean designing the abstraction
against exactly one working example and one unwritten one.

So: build it standalone. Revisit the extraction once both have run in
production for a while and the real differences are known rather than guessed.
Recording it here so it reads as a decision rather than an oversight.

## Sequencing

Each step leaves the tree working and tested.

1. **Parameterise the MCP read timeout.** Pure refactor, no behaviour change,
   existing tests untouched.
2. **Add the podcast tool allowlists and both builders.** Test that the
   interactive tool set never contains the Whisper tool.
3. **Add the discovery and publisher-transcript path**, with the budget. No
   Whisper yet. Test the budget ceiling, the `source_id`/`query_used` mapping,
   sequential processing, and paging to the end.
4. **Add the Whisper fallback.** Test that it fires only on
   `no_transcript_found` on the first page, never after a successful page, never
   on `not_configured` or `whisper_unavailable`, and that paging afterwards uses
   the ordinary transcript tool.
5. **Add the scheduled job**, report formatter, and `transcript_backend` in the
   report.
6. **Write the contract document** and record the outcome in the history.

Step 4 is the one that matters. The invariant it protects — never overwrite a
publisher transcript — is deterministic and belongs in pytest. Whether Whisper
summaries are any good belongs in the evaluation set, and needs a case file that
does not yet exist.

## The parked YouTube Whisper plan is now dead

[youtube-transcription-plan.md](youtube-transcription-plan.md) was parked on
2026-08-18 pending a re-count of caption failures after a month of real
scheduled runs.

That condition can never be met usefully. The plan assumed Net-Razor could
download YouTube audio, and it no longer can. It should be marked dead with that
reason rather than left looking like work waiting on a number.

Its findings survive here: the two-client timeout split, the timeout sitting
above the provider's own cap, transcription in its own node, and surfacing
`source_backend` in the report. All four are in this plan.
