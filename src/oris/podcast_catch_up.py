"""Fixed Podcast Catch-up workflow backed by Net-Razor MCP.

Built to share no code with the YouTube specialist it was a candidate
replacement for, on the reasoning that sharing would turn that specialist\'s
removal into an untangling rather than a deletion. Net-Razor dropped its
YouTube source on 2026-08-26 and ORIS followed the same day: every test in this
module passed before a line of it was touched, which is what the separation was
for.
"""

import json
from typing import Any, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.net_razor import PODCAST_CATCH_UP_TOOL_NAMES
from oris.podcast_output import PodcastEpisodeSummary
from oris.prompts import load_system_prompt
from oris.search import NonEmptyText
from oris.threat_reports import ThreatReportStore

EPISODE_SUMMARY_SYSTEM_PROMPT = load_system_prompt("podcast_episode_summary_system.txt")
CATCH_UP_SYSTEM_PROMPT = load_system_prompt("podcast_catch_up_system.txt")

DEFAULT_MAX_EPISODES = 5
MAX_EPISODES = 10
SUMMARY_TIMEOUT_SECONDS = 1800

NO_TRANSCRIPT_ERROR = "no_transcript_found"
"""The one Net-Razor error that means "try transcribing this yourself".

Every other error type is terminal for that episode. Branching on the published
`type` string is reading Net-Razor's classification; deciding for ourselves what
an error message means would be rebuilding it.
"""

MAX_TRANSCRIPT_PARTS_PER_RUN = 60
"""How many transcript parts one run will read across all its episodes.

An episode is read to its end. What is bounded is the run, which is the thing
the budget was always meant to protect: Net-Razor cannot know how many model
calls one run can afford, but it cannot know that per episode either.

This used to be a per-episode page cap, and that was the wrong axis. It punished
exactly one thing — a long episode — while short ones left the budget unused,
and it cut the same weekly show short twice, at six pages and again at eight,
having prevented nothing in between.

Derived from the other limits rather than from a sample. `MAX_EPISODES` is ten,
a realistic long run averages six parts an episode, and summarising one part
measured at about sixteen seconds, so sixty parts is roughly sixteen minutes —
inside the summarising node's thirty-minute bound with room to spare. The
ceiling exists at all because the alternative guard is that timeout, and it
kills the whole digest rather than trimming one episode.
"""


class TranscriptSummary(BaseModel):
    """Structured summary of one supplied transcript part."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: NonEmptyText


class PodcastCatchUpAnswer(BaseModel):
    """Structured final digest returned by the local model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: NonEmptyText = Field(description="Concise digest text without URLs.")
    cited_urls: tuple[NonEmptyText, ...] = Field(
        description="Canonical episode URLs supporting the digest."
    )


class PodcastCatchUpInput(TypedDict):
    """Public input accepted by Podcast Catch-up."""

    days: NotRequired[int]
    max_episodes: NotRequired[int]
    show: NotRequired[str]
    thread_id: NotRequired[str]
    include_processed: NotRequired[bool]


class PodcastCatchUpOutput(TypedDict):
    """Public JSON-compatible output returned by Podcast Catch-up."""

    answer: str
    cited_urls: list[str]
    episodes: list[PodcastEpisodeSummary]
    caveats: list[str]


class PreparedPodcastCatchUpOutput(PodcastCatchUpOutput):
    """Validated result plus internal receipts needed for acknowledgement."""

    transcript_call_ids: list[str]


class PodcastCatchUpState(TypedDict):
    """Internal state shared by Podcast Catch-up nodes."""

    days: NotRequired[int]
    max_episodes: NotRequired[int]
    show: NotRequired[str]
    thread_id: NotRequired[str]
    include_processed: NotRequired[bool]
    discovered_episodes: NotRequired[list[dict[str, Any]]]
    transcript_backends: NotRequired[dict[str, str]]
    transcribed_this_run: NotRequired[list[str]]
    transcript_call_ids: NotRequired[list[str]]
    episodes: NotRequired[list[PodcastEpisodeSummary]]
    caveats: NotRequired[list[str]]
    answer: NotRequired[str]
    cited_urls: NotRequired[list[str]]


def _structured_content(result: object) -> dict[str, Any]:
    """Unwrap the structured JSON the official MCP adapter attaches."""
    if not isinstance(result, ToolMessage):
        raise TypeError("Net-Razor did not return a LangChain ToolMessage")
    if not isinstance(result.artifact, dict):
        raise ValueError("Net-Razor did not return structured JSON")
    content = result.artifact.get("structured_content")
    if not isinstance(content, dict):
        raise ValueError("Net-Razor did not return structured JSON")
    return content


def _first_error_type(page: dict[str, Any]) -> str | None:
    """Read the error classification Net-Razor published, if it reported one."""
    errors = page.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    return first.get("type") if isinstance(first, dict) else None


def select_episodes(
    episodes: list[dict[str, Any]],
    max_episodes: int,
) -> list[dict[str, Any]]:
    """Spread the run budget across feeds instead of spending it newest-first.

    Net-Razor caps how many episodes each feed contributes, and flattening
    everything into one newest-first list threw that fairness away. Measured
    against the real feeds on 2026-08-25: a show publishing daily took six of
    eight slots and two weekly shows never appeared at all. Raising the budget
    does not help, because it only admits more of the same show before reaching
    anyone else.

    So take one episode from each feed before a second from any, keeping each
    feed's own newest-first order and the order the feeds arrived in. A feed
    that runs out drops away and the rest keep going round, so a budget larger
    than the number of feeds still fills.
    """
    by_feed: dict[str, list[dict[str, Any]]] = {}
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError("Net-Razor returned an invalid episode entry")
        by_feed.setdefault(episode["query_used"], []).append(episode)

    selected: list[dict[str, Any]] = []
    while len(selected) < max_episodes:
        took_one = False
        for queue in by_feed.values():
            if not queue:
                continue
            selected.append(queue.pop(0))
            took_one = True
            if len(selected) == max_episodes:
                break
        if not took_one:
            break
    return selected


def _episode_reference(episode: dict[str, Any]) -> dict[str, str]:
    """Map one discovery item onto the arguments the transcript tools take.

    Net-Razor's discovery items are its generic evidence shape, so the two
    fields the transcript tools require are not named after them: the episode ID
    arrives as `source_id` and the feed URL as `query_used`.
    """
    return {
        "episode_id": episode["source_id"],
        "feed_url": episode["query_used"],
    }


def create_podcast_catch_up_preparation_graph(
    discovery_tool: BaseTool,
    transcript_tool: BaseTool,
    model: BaseChatModel,
    *,
    transcription_tool: BaseTool | None = None,
    transcribe_catch_ups: bool = True,
    report_store: ThreatReportStore | None = None,
) -> CompiledStateGraph:
    """Compile podcast discovery, transcription, summaries, and synthesis.

    Without `transcription_tool` the graph degrades to publisher transcripts,
    and an episode with none becomes a caveat — which is also what happens when
    Net-Razor has transcription switched off.

    `transcribe_catch_ups` is what separates a run someone is sitting in front
    of from one nobody is waiting on. Chat sets it False: naming a show is at
    most one episode and so at most one transcription, which the MCP deadline
    already bounds, but a whole catch-up could transcribe five in a row and no
    chat turn should hold the interface for that long. The scheduled run has
    nobody waiting and keeps the default.
    """
    actual_tool_names = (discovery_tool.name, transcript_tool.name)
    preparation_tool_names = PODCAST_CATCH_UP_TOOL_NAMES[:2]
    if actual_tool_names != preparation_tool_names:
        raise ValueError(
            "Podcast Catch-up preparation requires tools in this order: "
            f"{', '.join(preparation_tool_names)}"
        )

    summary_model = model.with_structured_output(
        TranscriptSummary,
        method="json_schema",
    )
    digest_model = model.with_structured_output(
        PodcastCatchUpAnswer,
        method="json_schema",
    )

    async def read_transcript_page(
        tool: BaseTool,
        episode: dict[str, Any],
        offset: int,
    ) -> dict[str, Any]:
        return _structured_content(
            await tool.ainvoke(
                {
                    "type": "tool_call",
                    "id": str(uuid4()),
                    "name": tool.name,
                    "args": {**_episode_reference(episode), "offset": offset},
                }
            )
        )

    async def discover_episodes(
        state: PodcastCatchUpState,
    ) -> dict[str, object]:
        max_episodes = state.get("max_episodes", DEFAULT_MAX_EPISODES)
        if not isinstance(max_episodes, int) or not 1 <= max_episodes <= MAX_EPISODES:
            raise ValueError(f"max_episodes must be between 1 and {MAX_EPISODES}")

        # A catch-up asks for what Net-Razor has not yet handed over. A recap
        # asks for episodes it already has, which is the only way to read a
        # scheduled run's work again: that run acknowledged its episodes, so
        # they are gone from the catch-up queue by morning.
        tool_args: dict[str, object] = {
            "include_processed": state.get("include_processed", False)
        }
        if "days" in state:
            tool_args["days"] = state["days"]

        discovery_result = _structured_content(
            await discovery_tool.ainvoke(
                {
                    "type": "tool_call",
                    "id": str(uuid4()),
                    "name": discovery_tool.name,
                    "args": tool_args,
                }
            )
        )

        episodes = discovery_result.get("items")
        if not isinstance(episodes, list):
            raise ValueError("Net-Razor did not return an episode list")

        # Podcast discovery reports a feed it could not read as an entry in
        # `errors`, not as a `caveats` list. A run that silently covered six of
        # eight feeds would look identical to one that covered all eight.
        returned_errors = discovery_result.get("errors", [])
        caveats = [
            f"Feed problem: {error['message']}"
            for error in returned_errors
            if isinstance(error, dict) and isinstance(error.get("message"), str)
        ]
        show = state.get("show", "").strip()
        if show:
            # Matched on the display name Net-Razor already returns, so ORIS
            # never needs to know a feed URL. Narrowing to a configured show is
            # not the same as accepting an arbitrary feed, so the boundary the
            # contract sets still holds.
            matching = [
                episode
                for episode in episodes
                if show.casefold() in episode["author"]["display_name"].casefold()
            ]
            # One show means one episode. Asking about a specific podcast is
            # asking what its latest instalment said, not for a catch-up.
            return {
                "max_episodes": max_episodes,
                "discovered_episodes": matching[:1],
                "caveats": caveats,
            }
        return {
            "max_episodes": max_episodes,
            "discovered_episodes": select_episodes(episodes, max_episodes),
            "caveats": caveats,
        }

    async def obtain_transcripts(
        state: PodcastCatchUpState,
    ) -> dict[str, object]:
        """Make sure each episode has a transcript, transcribing where needed.

        The publisher's transcript is always asked for first, and transcription
        is reachable from exactly one branch: the first page came back with
        `no_transcript_found`. Net-Razor's store is first-writer-wins, so
        transcribing an episode whose publisher transcript was never fetched
        forecloses that better version permanently — it usually identifies who
        is speaking, and machine transcription never does.

        The decision is made on the first page only. A later page failing means
        a transcript already exists, and falling back there would trade it away
        to recover one page.

        This node has no timeout of its own. Every call it makes carries an MCP
        deadline and the number of calls is bounded by the episode budget, so
        the product already bounds the node. The summarising node is different:
        model calls carry no such deadline.
        """
        backends: dict[str, str] = {}
        # Which transcripts this run made, as opposed to found. The backend
        # alone cannot answer that: a Whisper transcript from last night's
        # scheduled run is served straight from Net-Razor's store and looks
        # identical to one produced a minute ago.
        transcribed_this_run: list[str] = []
        caveats = list(state["caveats"])
        # Collected per show and reported once at the end. Publishing no
        # transcript is a fact about a feed, not about an episode, and a feed
        # with five new episodes was saying it five times over — which buried
        # every other caveat in the run under repetition.
        no_transcription_available: list[str] = []
        transcription_needs_a_name: list[str] = []
        not_transcribed_yet: list[str] = []
        recap = state.get("include_processed", False)

        for episode in state["discovered_episodes"]:
            if not isinstance(episode, dict):
                raise ValueError("Net-Razor returned an invalid episode entry")

            title = episode["title"]
            show_name = episode["author"]["display_name"]
            page = await read_transcript_page(transcript_tool, episode, 0)
            error_type = _first_error_type(page)

            if error_type is None:
                backends[episode["source_id"]] = page.get("source_backend", "publisher")
                continue

            if error_type != NO_TRANSCRIPT_ERROR:
                caveats.append(f"Transcript unavailable for {title}: {error_type}.")
                continue

            # A recap reads transcripts that already exist and never makes
            # one. Transcribing here would quietly turn "show me what last
            # night produced" into another hour of Whisper.
            if recap:
                if show_name not in not_transcribed_yet:
                    not_transcribed_yet.append(show_name)
                continue

            if transcription_tool is None:
                if show_name not in no_transcription_available:
                    no_transcription_available.append(show_name)
                continue

            if not transcribe_catch_ups and not state.get("show", "").strip():
                if show_name not in transcription_needs_a_name:
                    transcription_needs_a_name.append(show_name)
                continue

            transcribed = await read_transcript_page(transcription_tool, episode, 0)
            transcription_error = _first_error_type(transcribed)
            if transcription_error is not None:
                caveats.append(f"Could not transcribe {title}: {transcription_error}.")
                continue

            backends[episode["source_id"]] = transcribed.get(
                "source_backend", "whisper"
            )
            transcribed_this_run.append(episode["source_id"])

        # The show's name, never the episode's: the advice is to ask for it by
        # name, and naming a show is what the narrowing matches on. Printing the
        # episode title made the instruction impossible to follow.
        for show_name in no_transcription_available:
            caveats.append(
                f"{show_name} publishes no transcript, and transcription is "
                "not available on this path."
            )
        for show_name in transcription_needs_a_name:
            caveats.append(
                f"{show_name} publishes no transcript. Run "
                f"`/podcasts {show_name}` to have its newest episode "
                "transcribed."
            )
        for show_name in not_transcribed_yet:
            caveats.append(
                f"{show_name} has an episode nothing has transcribed yet, so "
                "this recap does not cover it."
            )
        return {
            "transcript_backends": backends,
            "transcribed_this_run": transcribed_this_run,
            "caveats": caveats,
        }

    async def summarize_part(
        episode: dict[str, Any],
        part: dict[str, Any],
        backend: str,
    ) -> str:
        """Summarize one part of a transcript, never the whole of a long one.

        Keeping a single part in context is the reason Net-Razor pages at all.
        Joining the parts first and summarizing once would rebuild exactly the
        oversized input the paging contract exists to avoid.
        """
        response = await summary_model.ainvoke(
            [
                ("system", EPISODE_SUMMARY_SYSTEM_PROMPT),
                (
                    "human",
                    json.dumps(
                        {
                            "title": episode["title"],
                            "show": episode["author"]["display_name"],
                            "published_at": episode["published_at"],
                            "transcript_backend": backend,
                            "transcript_part": part.get("part"),
                            "transcript_part_count": part.get("part_count"),
                            "transcript": part["text"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            ],
            max_completion_tokens=320,
        )
        return response.summary

    async def summarize_episodes(
        state: PodcastCatchUpState,
    ) -> dict[str, object]:
        summaries: list[PodcastEpisodeSummary] = []
        transcripts: list[dict[str, Any]] = []
        transcript_call_ids: list[str] = []
        caveats = list(state["caveats"])
        backends = state["transcript_backends"]
        made_here = set(state["transcribed_this_run"])
        parts_remaining = MAX_TRANSCRIPT_PARTS_PER_RUN

        for episode in state["discovered_episodes"]:
            backend = backends.get(episode["source_id"])
            if backend is None:
                # Already reported as a caveat by the transcription node.
                continue

            # Every page here is served from Net-Razor's store, because the
            # transcription node already fetched or produced page one. Follow
            # `next_offset` to the end rather than stopping at the first page,
            # which would summarise an episode from its opening minutes and
            # then acknowledge it as done.
            part_summaries: list[str] = []
            transcript_parts: list[str] = []
            transcript_call_id = ""
            unreadable: str | None = None
            offset: int | None = 0

            while offset is not None and parts_remaining > 0:
                part = await read_transcript_page(transcript_tool, episode, offset)
                text = part.get("text")
                if _first_error_type(part) is not None or not isinstance(text, str):
                    unreadable = (
                        f"Transcript unavailable for {episode['title']}."
                        if not part_summaries
                        else f"Transcript incomplete for {episode['title']}: "
                        f"Net-Razor did not return part {len(part_summaries) + 1}."
                    )
                    break
                if not text.strip():
                    unreadable = (
                        f"Transcript empty for {episode['title']}."
                        if not part_summaries
                        else f"Transcript incomplete for {episode['title']}: "
                        f"part {len(part_summaries) + 1} was empty."
                    )
                    break

                if not part_summaries:
                    # One acknowledgement per episode: Net-Razor resolves any
                    # successful transcript call back to its episode, so the
                    # first part's receipt marks the whole episode processed.
                    transcript_call_id = part.get("call_id")
                    if (
                        not isinstance(transcript_call_id, str)
                        or not transcript_call_id
                    ):
                        raise ValueError(
                            "Net-Razor did not return a transcript call ID"
                        )

                transcript_parts.append(text)
                part_summaries.append(await summarize_part(episode, part, backend))
                parts_remaining -= 1
                next_offset = part.get("next_offset")
                offset = next_offset if isinstance(next_offset, int) else None

            truncated = offset is not None
            if unreadable is not None:
                caveats.append(unreadable)
            elif truncated:
                caveats.append(f"Transcript truncated for {episode['title']}.")
            if not part_summaries:
                continue

            transcripts.append(
                {
                    "title": episode["title"],
                    "show": episode["author"]["display_name"],
                    "published_at": episode["published_at"],
                    "url": episode["canonical_url"],
                    "transcript_backend": backend,
                    "transcript_created_now": episode["source_id"] in made_here,
                    "transcript_truncated": truncated,
                    "transcript": "".join(transcript_parts),
                }
            )
            summaries.append(
                {
                    "episode_id": episode["source_id"],
                    "title": episode["title"],
                    "show": episode["author"]["display_name"],
                    "published_at": episode["published_at"],
                    "url": episode["canonical_url"],
                    "summary": "\n\n".join(part_summaries),
                    "transcript_backend": backend,
                    "transcript_created_now": episode["source_id"] in made_here,
                    "transcript_truncated": truncated,
                }
            )
            transcript_call_ids.append(transcript_call_id)

        # Said once for the run, not once per episode. Which episodes were
        # machine-transcribed is now on each episode's own line, so repeating
        # the warning per episode only buried the other caveats.
        if any(
            backends.get(summary["episode_id"]) == "whisper" for summary in summaries
        ):
            caveats.append(
                "Machine-transcribed episodes are marked below. Names, "
                "acronyms, and version numbers in those are less reliable."
            )

        _store_transcripts(state, transcripts)
        return {
            "episodes": summaries,
            "caveats": caveats,
            "transcript_call_ids": transcript_call_ids,
        }

    def _store_transcripts(
        state: PodcastCatchUpState,
        transcripts: list[dict[str, Any]],
    ) -> None:
        """Keep what the summaries were made from, so it can be read afterwards.

        A summary always prompts the question a summary cannot answer: what did
        the episode actually say. This is the same reason Threat Intel stores
        full provider responses, and it uses the same store, so `/threat` and
        `/podcasts` runs are opened the same way from the same key.

        The transcript is written out rather than fetched back from Net-Razor on
        demand. Net-Razor does still hold it, so this is a second copy — but
        fetching would mean the interface holding MCP tools, a page at a time,
        and evidence that cannot be read when Net-Razor is unavailable. Against
        that, a run's transcripts are a few hundred kilobytes and age out on the
        same retention schedule as everything else in the store.
        """
        if report_store is None or not transcripts:
            return
        shows = sorted({str(episode["show"]) for episode in transcripts})
        report_store.save(
            f"podcasts {' '.join(shows)}".strip(),
            {"episodes": transcripts},
            thread_id=state.get("thread_id", ""),
        )

    async def create_digest(state: PodcastCatchUpState) -> dict[str, object]:
        if not state["discovered_episodes"]:
            show = state.get("show", "").strip()
            answer = (
                f"No new episodes were found for a show matching '{show}'."
                if show
                else "No new podcast episodes were found."
            )
            return {"answer": answer, "cited_urls": []}
        if not state["episodes"]:
            return {
                "answer": "No usable podcast transcripts were available.",
                "cited_urls": [],
            }

        # One digest per show, each written from that show's episodes alone.
        #
        # A single digest across every feed lets the biggest subject win. The
        # feeds are not one subject: basketball, politics and Linux have no
        # agreements or connections to draw out, and asking for them produced a
        # digest about whichever show published most that week while the rest
        # went unmentioned. Sectioning it is not formatting — a show cannot be
        # crowded out of a call that only contains it.
        by_show: dict[str, list[PodcastEpisodeSummary]] = {}
        for episode in state["episodes"]:
            by_show.setdefault(episode["show"], []).append(episode)

        sections: list[str] = []
        cited: list[str] = []
        for show, episodes in by_show.items():
            response = await digest_model.ainvoke(
                [
                    ("system", CATCH_UP_SYSTEM_PROMPT),
                    (
                        "human",
                        json.dumps(
                            {"show": show, "episodes": episodes},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ),
                ],
                max_completion_tokens=400,
            )
            sections.append(f"## {show}\n\n{response.answer}")
            cited.extend(response.cited_urls)

        # Order preserved and duplicates dropped: an episode cited in its own
        # section is the only place it should appear.
        return {
            "answer": "\n\n".join(sections),
            "cited_urls": list(dict.fromkeys(cited)),
        }

    def validate_citations(state: PodcastCatchUpState) -> dict:
        """Reject an invented citation; report a missing one and keep going.

        The two failures are not equal. Citing a URL that was never supplied is
        fabrication and stays fatal. Citing nothing is a formatting miss, and
        the report already lists every episode with its canonical URL in its
        own section, so the digest remains traceable without it. Failing there
        would discard a finished digest — a whole night's, for a scheduled run
        — to protect something the reader already has.

        Web Research is deliberately stricter: its sources exist nowhere else
        in the output, so an uncited claim there cannot be checked at all.
        """
        available_urls = {episode["url"] for episode in state["episodes"]}
        cited_urls = set(state["cited_urls"])
        unsupported_urls = sorted(cited_urls - available_urls)
        if unsupported_urls:
            raise ValueError(
                f"The podcast digest cited unavailable URLs: {unsupported_urls}"
            )
        if available_urls and not cited_urls:
            return {
                "caveats": [
                    *state["caveats"],
                    "The digest cites no episode; see the episode list below "
                    "for what it was built from.",
                ]
            }
        return {}

    builder = StateGraph(
        PodcastCatchUpState,
        input_schema=PodcastCatchUpInput,
        output_schema=PreparedPodcastCatchUpOutput,
    )
    builder.add_node("discover_episodes", discover_episodes)
    builder.add_node("obtain_transcripts", obtain_transcripts)
    # Model calls carry no MCP deadline, so nothing else bounds this node. An
    # unattended run that hangs here keeps its `max_instances=1` slot and every
    # later firing is skipped in silence.
    builder.add_node(
        "summarize_episodes", summarize_episodes, timeout=SUMMARY_TIMEOUT_SECONDS
    )
    builder.add_node("create_digest", create_digest)
    builder.add_node("validate_citations", validate_citations)
    builder.add_edge(START, "discover_episodes")
    builder.add_edge("discover_episodes", "obtain_transcripts")
    builder.add_edge("obtain_transcripts", "summarize_episodes")
    builder.add_edge("summarize_episodes", "create_digest")
    builder.add_edge("create_digest", "validate_citations")
    builder.add_edge("validate_citations", END)
    return builder.compile()


async def acknowledge_podcast_catch_up(
    acknowledgement_tool: BaseTool,
    transcript_call_ids: list[str],
) -> None:
    """Mark completed transcript calls processed through Net-Razor."""
    expected_tool_name = PODCAST_CATCH_UP_TOOL_NAMES[2]
    if acknowledgement_tool.name != expected_tool_name:
        raise ValueError(
            f"Podcast Catch-up acknowledgement requires tool: {expected_tool_name}"
        )
    if not transcript_call_ids:
        return
    await acknowledgement_tool.ainvoke(
        {
            "type": "tool_call",
            "id": str(uuid4()),
            "name": acknowledgement_tool.name,
            "args": {"call_ids": transcript_call_ids},
        }
    )


def create_acknowledging_podcast_catch_up_graph(
    preparation_graph: CompiledStateGraph,
    acknowledgement_tool: BaseTool,
) -> CompiledStateGraph:
    """Wrap preparation with immediate acknowledgement for interactive use."""

    async def prepare(state: PodcastCatchUpState) -> dict:
        request = {
            key: state[key]
            for key in (
                "days",
                "max_episodes",
                "show",
                "thread_id",
                "include_processed",
            )
            if key in state
        }
        return await preparation_graph.ainvoke(request)

    async def mark_processed(state: PodcastCatchUpState) -> dict:
        """Acknowledge processed transcripts without risking the finished digest.

        Acknowledgement is the last step and is deliberately non-fatal. A
        validated digest already exists by this point, and failing here would
        discard it while leaving some episodes acknowledged. The safe direction
        is for an episode to appear again, never to vanish.

        A recap acknowledges nothing. Its episodes were acknowledged by
        whatever produced them, and a recap that happened to pick up a new
        episode with a publisher transcript would otherwise mark it processed
        and take it out of the next real catch-up without ever transcribing it.
        """
        if state.get("include_processed", False):
            return {}
        try:
            await acknowledge_podcast_catch_up(
                acknowledgement_tool,
                state["transcript_call_ids"],
            )
        except Exception as error:
            return {
                "caveats": [
                    *state["caveats"],
                    "Net-Razor did not record these episodes as processed "
                    f"({type(error).__name__}: {error}); they may appear again.",
                ]
            }
        return {}

    builder = StateGraph(
        PodcastCatchUpState,
        input_schema=PodcastCatchUpInput,
        output_schema=PodcastCatchUpOutput,
    )
    builder.add_node("prepare", prepare)
    builder.add_node("mark_processed", mark_processed)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "mark_processed")
    builder.add_edge("mark_processed", END)
    return builder.compile()


def create_podcast_catch_up_graph(
    discovery_tool: BaseTool,
    transcript_tool: BaseTool,
    acknowledgement_tool: BaseTool,
    model: BaseChatModel,
    *,
    transcription_tool: BaseTool | None = None,
    report_store: ThreatReportStore | None = None,
) -> CompiledStateGraph:
    """Compile Podcast Catch-up for chat, transcribing only a named show.

    Three of the five feeds in real use publish no transcript at all, so a chat
    that could never transcribe answered "nothing to summarise" for most of the
    shows anyone would name. One episode is a wait chat can afford; a catch-up
    is not, which `transcribe_catch_ups` is what decides.
    """
    preparation_graph = create_podcast_catch_up_preparation_graph(
        discovery_tool,
        transcript_tool,
        model,
        transcription_tool=transcription_tool,
        transcribe_catch_ups=False,
        report_store=report_store,
    )
    return create_acknowledging_podcast_catch_up_graph(
        preparation_graph,
        acknowledgement_tool,
    )
