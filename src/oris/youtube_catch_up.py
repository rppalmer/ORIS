"""Fixed YouTube Catch-up workflow backed by Net-Razor MCP."""

import json
from typing import Any, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.net_razor import YOUTUBE_CATCH_UP_TOOL_NAMES
from oris.prompts import load_system_prompt
from oris.search import NonEmptyText

VIDEO_SUMMARY_SYSTEM_PROMPT = load_system_prompt("youtube_video_summary_system.txt")
CATCH_UP_SYSTEM_PROMPT = load_system_prompt("youtube_catch_up_system.txt")

DEFAULT_MAX_VIDEOS = 5
MAX_VIDEOS = 10
SUMMARY_TIMEOUT_SECONDS = 900

MAX_TRANSCRIPT_PARTS = 3
"""How many parts of one video's transcript a single run will read.

Net-Razor serves a long transcript in parts of about 40 KB, so three covers
roughly two and a quarter hours of speech and nearly every video in practice.
This is an orchestration budget rather than a provider limit: Net-Razor cannot
know how many model calls one catch-up run can afford, and the run as a whole
has a fixed timeout. A video that exceeds it is summarised from the parts that
were read and reported as truncated.
"""


class TranscriptSummary(BaseModel):
    """Structured summary of one supplied transcript."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: NonEmptyText


class YouTubeCatchUpAnswer(BaseModel):
    """Structured final digest returned by the local model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: NonEmptyText = Field(description="Concise digest text without URLs.")
    cited_urls: tuple[NonEmptyText, ...] = Field(
        description="Canonical YouTube URLs supporting the digest."
    )


class YouTubeVideoSummary(TypedDict):
    """One summarized video returned in public graph output."""

    video_id: str
    title: str
    channel: str
    published_at: str
    url: str
    summary: str
    transcript_truncated: bool


class YouTubeCatchUpInput(TypedDict):
    """Public input accepted by YouTube Catch-up."""

    days: NotRequired[int]
    max_videos: NotRequired[int]


class YouTubeCatchUpOutput(TypedDict):
    """Public JSON-compatible output returned by YouTube Catch-up."""

    answer: str
    cited_urls: list[str]
    videos: list[YouTubeVideoSummary]
    caveats: list[str]


class PreparedYouTubeCatchUpOutput(YouTubeCatchUpOutput):
    """Validated result plus internal receipts needed for acknowledgement."""

    transcript_call_ids: list[str]


class YouTubeCatchUpState(TypedDict):
    """Internal state shared by YouTube Catch-up nodes."""

    days: NotRequired[int]
    max_videos: NotRequired[int]
    discovered_videos: NotRequired[list[dict[str, Any]]]
    transcript_call_ids: NotRequired[list[str]]
    videos: NotRequired[list[YouTubeVideoSummary]]
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


def create_youtube_catch_up_preparation_graph(
    discovery_tool: BaseTool,
    transcript_tool: BaseTool,
    model: BaseChatModel,
) -> CompiledStateGraph:
    """Compile YouTube discovery, summaries, synthesis, and validation."""
    actual_tool_names = (discovery_tool.name, transcript_tool.name)
    preparation_tool_names = YOUTUBE_CATCH_UP_TOOL_NAMES[:2]
    if actual_tool_names != preparation_tool_names:
        raise ValueError(
            "YouTube Catch-up preparation requires tools in this order: "
            f"{', '.join(preparation_tool_names)}"
        )

    summary_model = model.with_structured_output(
        TranscriptSummary,
        method="json_schema",
    )
    digest_model = model.with_structured_output(
        YouTubeCatchUpAnswer,
        method="json_schema",
    )

    async def discover_videos(
        state: YouTubeCatchUpState,
    ) -> dict[str, object]:
        max_videos = state.get("max_videos", DEFAULT_MAX_VIDEOS)
        if not isinstance(max_videos, int) or not 1 <= max_videos <= MAX_VIDEOS:
            raise ValueError(f"max_videos must be between 1 and {MAX_VIDEOS}")

        tool_args: dict[str, object] = {"include_processed": False}
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

        videos = discovery_result.get("videos")
        if not isinstance(videos, list):
            raise ValueError("Net-Razor did not return a video list")
        returned_caveats = discovery_result.get("caveats", [])
        caveats = (
            [item for item in returned_caveats if isinstance(item, str)]
            if isinstance(returned_caveats, list)
            else []
        )
        return {
            "max_videos": max_videos,
            "discovered_videos": videos[:max_videos],
            "caveats": caveats,
        }

    async def read_transcript_part(url: str, offset: int) -> dict[str, Any]:
        return _structured_content(
            await transcript_tool.ainvoke(
                {
                    "type": "tool_call",
                    "id": str(uuid4()),
                    "name": transcript_tool.name,
                    "args": {
                        "url": url,
                        "include_segments": False,
                        "offset": offset,
                    },
                }
            )
        )

    async def summarize_part(video: dict[str, Any], part: dict[str, Any]) -> str:
        """Summarize one part of a transcript, never the whole of a long one.

        Keeping a single part in context is the reason Net-Razor pages at all.
        Joining the parts first and summarizing once would rebuild exactly the
        oversized input the paging contract exists to avoid.
        """
        response = await summary_model.ainvoke(
            [
                ("system", VIDEO_SUMMARY_SYSTEM_PROMPT),
                (
                    "human",
                    json.dumps(
                        {
                            "title": video["title"],
                            "channel": video["channel_title"],
                            "published_at": video["published_at"],
                            "transcript_part": part.get("part"),
                            "transcript_part_count": part.get("part_count"),
                            "transcript": part["text"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            ],
            max_completion_tokens=256,
        )
        return response.summary

    async def summarize_videos(
        state: YouTubeCatchUpState,
    ) -> dict[str, object]:
        summaries: list[YouTubeVideoSummary] = []
        transcript_call_ids: list[str] = []
        caveats = list(state["caveats"])

        for video in state["discovered_videos"]:
            if not isinstance(video, dict):
                raise ValueError("Net-Razor returned an invalid video entry")

            # A long transcript arrives in parts, and reading only the first
            # one lost the rest of the video for good: it was summarized from
            # its opening minutes and then marked processed, so it never came
            # back in the queue. Follow `next_offset` to the end instead. Parts
            # after the first are served from Net-Razor's local storage, so
            # paging costs nothing upstream.
            part_summaries: list[str] = []
            transcript_call_id = ""
            unreadable: str | None = None
            offset: int | None = 0

            while offset is not None and len(part_summaries) < MAX_TRANSCRIPT_PARTS:
                part = await read_transcript_part(video["url"], offset)
                text = part.get("text")
                if part.get("errors") or not isinstance(text, str):
                    unreadable = (
                        f"Transcript unavailable for {video['title']}."
                        if not part_summaries
                        else f"Transcript incomplete for {video['title']}: "
                        f"Net-Razor did not return part {len(part_summaries) + 1}."
                    )
                    break
                if not text.strip():
                    unreadable = (
                        f"Transcript empty for {video['title']}."
                        if not part_summaries
                        else f"Transcript incomplete for {video['title']}: "
                        f"part {len(part_summaries) + 1} was empty."
                    )
                    break

                if not part_summaries:
                    # One acknowledgement per video: Net-Razor resolves any
                    # successful transcript call back to its video, so the
                    # first part's receipt marks the whole video processed.
                    transcript_call_id = part.get("call_id")
                    if (
                        not isinstance(transcript_call_id, str)
                        or not transcript_call_id
                    ):
                        raise ValueError(
                            "Net-Razor did not return a transcript call ID"
                        )

                part_summaries.append(await summarize_part(video, part))
                next_offset = part.get("next_offset")
                offset = next_offset if isinstance(next_offset, int) else None

            # `truncated` on a response only says the transcript came in more
            # than one part, and it is set on the final part too. The video is
            # genuinely cut short only when parts remained and we stopped.
            truncated = offset is not None
            if unreadable is not None:
                caveats.append(unreadable)
            elif truncated:
                caveats.append(f"Transcript truncated for {video['title']}.")
            if not part_summaries:
                continue

            summaries.append(
                {
                    "video_id": video["video_id"],
                    "title": video["title"],
                    "channel": video["channel_title"],
                    "published_at": video["published_at"],
                    "url": video["url"],
                    "summary": "\n\n".join(part_summaries),
                    "transcript_truncated": truncated,
                }
            )
            transcript_call_ids.append(transcript_call_id)

        return {
            "videos": summaries,
            "caveats": caveats,
            "transcript_call_ids": transcript_call_ids,
        }

    async def create_digest(
        state: YouTubeCatchUpState,
    ) -> dict[str, object]:
        if not state["discovered_videos"]:
            return {
                "answer": "No new YouTube videos were found.",
                "cited_urls": [],
            }
        if not state["videos"]:
            return {
                "answer": "No usable YouTube transcripts were available.",
                "cited_urls": [],
            }

        response = await digest_model.ainvoke(
            [
                ("system", CATCH_UP_SYSTEM_PROMPT),
                (
                    "human",
                    json.dumps(
                        {
                            "videos": state["videos"],
                            "caveats": state["caveats"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            ],
            max_completion_tokens=512,
        )
        return {
            "answer": response.answer,
            "cited_urls": list(response.cited_urls),
        }

    def validate_citations(state: YouTubeCatchUpState) -> dict:
        available_urls = {video["url"] for video in state["videos"]}
        cited_urls = set(state["cited_urls"])
        unsupported_urls = sorted(cited_urls - available_urls)
        if unsupported_urls:
            raise ValueError(
                f"The YouTube digest cited unavailable URLs: {unsupported_urls}"
            )
        if available_urls and not cited_urls:
            raise ValueError("The YouTube digest must include at least one cited URL")
        return {}

    builder = StateGraph(
        YouTubeCatchUpState,
        input_schema=YouTubeCatchUpInput,
        output_schema=PreparedYouTubeCatchUpOutput,
    )
    builder.add_node("discover_videos", discover_videos)
    # One node holds up to ten transcript fetches and ten model calls, so the
    # per-request MCP timeout bounds each call but nothing bounds the node.
    # Unattended scheduled runs are where that matters: a hung job keeps its
    # `max_instances=1` slot and every later firing is skipped in silence.
    builder.add_node(
        "summarize_videos", summarize_videos, timeout=SUMMARY_TIMEOUT_SECONDS
    )
    builder.add_node("create_digest", create_digest)
    builder.add_node("validate_citations", validate_citations)
    builder.add_edge(START, "discover_videos")
    builder.add_edge("discover_videos", "summarize_videos")
    builder.add_edge("summarize_videos", "create_digest")
    builder.add_edge("create_digest", "validate_citations")
    builder.add_edge("validate_citations", END)
    return builder.compile()


async def acknowledge_youtube_catch_up(
    acknowledgement_tool: BaseTool,
    transcript_call_ids: list[str],
) -> None:
    """Mark completed transcript calls processed through Net-Razor."""
    expected_tool_name = YOUTUBE_CATCH_UP_TOOL_NAMES[2]
    if acknowledgement_tool.name != expected_tool_name:
        raise ValueError(
            f"YouTube Catch-up acknowledgement requires tool: {expected_tool_name}"
        )
    if not transcript_call_ids:
        return
    await acknowledgement_tool.ainvoke(
        {
            "type": "tool_call",
            "id": str(uuid4()),
            "name": acknowledgement_tool.name,
            "args": {"transcript_call_ids": transcript_call_ids},
        }
    )


def create_acknowledging_youtube_catch_up_graph(
    preparation_graph: CompiledStateGraph,
    acknowledgement_tool: BaseTool,
) -> CompiledStateGraph:
    """Wrap preparation with immediate acknowledgement for interactive use."""

    async def prepare(state: YouTubeCatchUpState) -> dict:
        request = {key: state[key] for key in ("days", "max_videos") if key in state}
        return await preparation_graph.ainvoke(request)

    async def mark_processed(state: YouTubeCatchUpState) -> dict:
        """Acknowledge processed transcripts without risking the finished digest.

        Acknowledgement is the last step and is deliberately non-fatal. A
        validated digest has already been produced by this point, and failing
        the run here would discard it while leaving some videos acknowledged.
        The safe direction is for a video to appear again, never to vanish.
        """
        try:
            await acknowledge_youtube_catch_up(
                acknowledgement_tool,
                state["transcript_call_ids"],
            )
        except Exception as error:
            return {
                "caveats": [
                    *state["caveats"],
                    "Net-Razor did not record these videos as processed "
                    f"({type(error).__name__}: {error}); they may appear again.",
                ]
            }
        return {}

    builder = StateGraph(
        YouTubeCatchUpState,
        input_schema=YouTubeCatchUpInput,
        output_schema=YouTubeCatchUpOutput,
    )
    builder.add_node("prepare", prepare)
    builder.add_node("mark_processed", mark_processed)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "mark_processed")
    builder.add_edge("mark_processed", END)
    return builder.compile()


def create_youtube_catch_up_graph(
    discovery_tool: BaseTool,
    transcript_tool: BaseTool,
    acknowledgement_tool: BaseTool,
    model: BaseChatModel,
) -> CompiledStateGraph:
    """Compile YouTube Catch-up with immediate interactive acknowledgement."""
    preparation_graph = create_youtube_catch_up_preparation_graph(
        discovery_tool,
        transcript_tool,
        model,
    )
    return create_acknowledging_youtube_catch_up_graph(
        preparation_graph,
        acknowledgement_tool,
    )
