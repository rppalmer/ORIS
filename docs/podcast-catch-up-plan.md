# Podcast Catch-up — the ORIS side

- Status: Planned, not started. Awaiting approval to build.
- Written: 2026-08-18
- Net-Razor: podcast source merged on `main` (`55ee11e`)
- Own contract, to be written. Deliberately not an addition to
  [youtube-catch-up-contract.md](youtube-catch-up-contract.md).

## Why this exists

YouTube audio can no longer be downloaded — proof-of-origin tokens, scrambled
URLs, and a streaming model that exposes no file. Podcast RSS has none of that:
a plain GET of a URL the publisher advertises. Spoken content now arrives
through podcasts, and eight feeds are configured.

**This is a candidate replacement for YouTube Catch-up, not a sibling.** Google
keeps making collection harder, the best plan anyone came up with was
inconsistent at its best and would need constant maintenance at its worst.
Podcasts with Whisper are the bet on a better path to the same data.

So YouTube Catch-up stays running for now, but nothing here is designed with it
in mind, and it may be deleted outright once podcasts prove out. That single
fact drives more of this design than any other — see "Built for a clean break".

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

## The timeout: 1380 seconds, tracking Net-Razor's 1230

Reviewed with Net-Razor on 2026-08-19 and settled.

The brief's "about 15 minutes" was below what Net-Razor can legitimately take.
One `podcast_whisper_transcript` call passes through **three** caps in sequence:

| Stage | Cap |
| --- | --- |
| Feed fetch, to find the audio URL | 30s |
| Audio download | 300s |
| Transcription subprocess | 900s |
| **Worst case** | **1230s (20.5 min)** |

ORIS uses **1380 seconds (23 minutes)**, clearing that by 150 seconds. The
principle is Net-Razor's from the YouTube review: their caps are the real
limits, and ORIS's read timeout is a backstop that should never fire. At 15
minutes ORIS would have abandoned calls that were still working correctly, and
traded a classified `transcription_timeout` for a dead MCP session.

Two corrections landed on the way here, both worth remembering:

- An earlier draft of this plan said 1080s. That cleared the transcription cap
  but not the download in front of it.
- The three-cap total was not a real ceiling until Net-Razor fixed it. The
  download had no total bound — httpx limits the gap between chunks, not the
  whole transfer — so a server trickling bytes slowly could have streamed a
  large episode for hours without tripping anything. It is now wrapped in a
  genuine budget.

**1230 is the number to track.** If any of the three values moves, Net-Razor
treats it as a contract change and says so.

## What Net-Razor confirmed

All four questions answered on 2026-08-19. Two were bugs on their side, now
fixed on `main`.

### The store is first-writer-wins, and the brief was wrong

Confirmed: both tools read the store before doing any work. Calling Whisper on
an episode that already has a publisher transcript returns the publisher's,
spends no CPU, and overwrites nothing — `source_backend` still says `publisher`.

So the danger is not clobbering stored data. It is **ordering on a fresh
episode**: whichever tool runs first stores its result, and every later call
returns that one. Calling Whisper first forecloses ever fetching the publisher's
better version.

That makes this plan's rule — publisher transcript first, Whisper only on
`no_transcript_found`, decided once on the first page — necessary and
sufficient. Net-Razor's README, architecture notes, and design spec have been
corrected, and the misleading "a Whisper transcript supersedes a publisher one"
wording is gone.

### Paging uses the ordinary transcript tool

Page one with Whisper, then page on with `podcast_transcript`. `source_backend`
keeps reporting `whisper`, because the backend is stored alongside the
transcript rather than inferred from which tool asked.

Paging with the Whisper tool would also be safe — it checks the store first too
— but `podcast_transcript` says what is meant.

### Acknowledgement does not care which tool produced the transcript

Both tools write an item keyed by episode ID, and `podcast_mark_processed`
resolves a call ID through that item.

One detail that simplifies ORIS: **a call that returned an error produces no
item**, so a failed call ID cannot be acknowledged. It comes back as
`unknown_call_id` without affecting the others in the batch. ORIS therefore does
not need to carefully filter receipts before acknowledging — collecting only
successful ones stays the intent, but a mistake there is harmless rather than
silently marking a failed episode processed.

### These are pinned by tests

Net-Razor added four tests covering exactly the behaviours above, so none of
them can drift silently: Whisper returning a stored publisher transcript,
acknowledgement accepting a call ID from either tool, paging keeping the
`whisper` backend, and the download's total budget.

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

## How a user reaches podcast catch-up

**`/podcasts` only. No `/videos`.**

This reverses half of the 2026-08-19 decision, which was made before podcasts
were understood as a replacement rather than a sibling.

The original reasoning was sound on its own terms: two catch-up destinations
make a bare "catch me up" ambiguous, so both should get a deterministic
override. But `/videos` would be a new command for a specialist that may be
deleted — work thrown away, and a change to YouTube's reachability motivated
entirely by podcasts, which is exactly the coupling this plan is trying to
avoid.

So:

- `/podcasts` is added. It is the deterministic override, and it is the one that
  survives whatever happens to YouTube.
- YouTube keeps its existing router-only access, unchanged.
- Both routing lines exist, so "catch up on my podcasts" and "catch up on my
  videos" each work in words.
- A bare "catch me up" stays a coin flip while both exist. That is a temporary
  cost of a temporary overlap, and it disappears on its own — either YouTube is
  deleted and podcasts are the only answer, or it stays and the ambiguity is
  worth one more command then.

`/podcasts` takes no argument. The specialist takes its scope from Net-Razor's
configured feeds, not from the request.

With this, the podcast work touches YouTube Catch-up nowhere at all.

## Built for a clean break

Podcast Catch-up will duplicate most of YouTube Catch-up: the paging loop, the
per-page summarising, the digest and citation validation, the acknowledgement
wrapper, the scheduled job runner, and the report formatter. The differences are
field names, one extra tool, one extra output field, and the timeout.

**Do not factor any of it out.** YouTube may be deleted, and sharing code with
something scheduled for deletion turns that deletion into an untangling. The
duplication is the cheaper side of the trade, and it is permanent rather than a
merge deferred to later.

Net-Razor reached the same conclusion independently. Its
`stored_podcast_transcript` carries the note: *"Deliberately separate from
`stored_transcript`, which is YouTube's. The two barely differ, but YouTube may
be removed once podcasts prove out, and sharing would turn that removal into an
untangling rather than a deletion."* Both sides of the boundary are now built on
the same assumption.

### The acceptance test

Deleting these, and nothing else, must leave every podcast test passing:

- `src/oris/youtube_catch_up.py` and `tests/test_youtube_catch_up.py`
- the two YouTube prompt files
- `YOUTUBE_CATCH_UP_TOOL_NAMES` and `load_youtube_catch_up_tools`
- the YouTube builders in `web_research_app.py`
- `YouTubeCatchUpScheduledJob`, its dispatch branch, and its report formatter
- the `youtube_catch_up` router destination, its routing line, and its rows in
  the command and label tables

If that deletion needs one edit inside a podcast file, the separation failed.

### The import boundary

`podcast_catch_up.py` imports **nothing** from `youtube_catch_up.py`. Not the
`_structured_content` helper, not a state type, not a constant. Copy what is
needed.

It also gets its **own prompt files** — `podcast_episode_summary_system.txt` and
`podcast_catch_up_system.txt`. Pointing at the YouTube prompts would be the
quietest possible entanglement and the easiest to miss.

Shared infrastructure is fine and is not what this rule is about: the MCP
connection module, the prompt loader, the knowledge repository, configuration,
and the chat and scheduler wiring are common ground that both plug into. What
must not be shared is anything describing how spoken content is fetched,
paged, summarised, or reported.

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

Whisper transcription for YouTube was parked on
2026-08-18 pending a re-count of caption failures after a month of real
scheduled runs.

That condition can never be met usefully. The plan assumed Net-Razor could
download YouTube audio, and it no longer can. It should be marked dead with that
reason rather than left looking like work waiting on a number.

Its findings survive here: the two-client timeout split, the timeout sitting
above the provider's own cap, transcription in its own node, and surfacing
`source_backend` in the report. All four are in this plan.
