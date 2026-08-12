"""Tests for the fixed YouTube Catch-up graph."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from oris.youtube_catch_up import (
    TranscriptSummary,
    YouTubeCatchUpAnswer,
    create_youtube_catch_up_graph,
    create_youtube_catch_up_preparation_graph,
)


def make_video(number: int) -> dict[str, str]:
    """Return one compact Net-Razor discovery item."""
    return {
        "channel_id": "channel-1",
        "channel_title": "Example Channel",
        "video_id": f"video-{number}",
        "url": f"https://www.youtube.com/watch?v=video-{number}",
        "title": f"Video {number}",
        "published_at": f"2026-08-0{number}T12:00:00+00:00",
    }


def tool_message(tool_name: str, content: dict) -> ToolMessage:
    """Wrap structured MCP content as the official adapter does."""
    return ToolMessage(
        content="Net-Razor returned structured data.",
        artifact={"structured_content": content},
        tool_call_id=f"{tool_name}-call",
        name=tool_name,
    )


def make_dependencies(
    *,
    videos: list[dict[str, str]],
    transcript_results: list[dict] | None = None,
    summaries: list[TranscriptSummary] | None = None,
    digest: YouTubeCatchUpAnswer | None = None,
) -> tuple[Mock, Mock, Mock, Mock, AsyncMock, AsyncMock]:
    """Create controlled Net-Razor and structured-model doubles."""
    discovery_tool = Mock(spec=BaseTool)
    discovery_tool.name = "net_razor_yt_new_videos"
    discovery_tool.ainvoke = AsyncMock(
        return_value=tool_message(
            discovery_tool.name,
            {
                "videos": videos,
                "caveats": ["Discovery caveat."] if videos else [],
            },
        )
    )

    transcript_tool = Mock(spec=BaseTool)
    transcript_tool.name = "net_razor_yt_transcript"
    results = transcript_results or [
        {
            "call_id": f"transcript-call-{number}",
            "text": f"Transcript {number}",
            "truncated": number == 2,
            "errors": [],
        }
        for number in range(1, len(videos) + 1)
    ]
    transcript_tool.ainvoke = AsyncMock(
        side_effect=[tool_message(transcript_tool.name, result) for result in results]
    )

    acknowledgement_tool = Mock(spec=BaseTool)
    acknowledgement_tool.name = "net_razor_yt_mark_processed"
    acknowledgement_tool.ainvoke = AsyncMock(
        return_value=tool_message(
            acknowledgement_tool.name,
            {
                "acknowledged_video_ids": [video["video_id"] for video in videos],
                "already_acknowledged_video_ids": [],
            },
        )
    )

    model = Mock(spec=BaseChatModel)
    summary_model = AsyncMock()
    summary_model.ainvoke.side_effect = summaries or [
        TranscriptSummary(summary=f"Summary {number}")
        for number in range(1, len(videos) + 1)
    ]
    digest_model = AsyncMock()
    digest_model.ainvoke.return_value = digest or YouTubeCatchUpAnswer(
        answer="Combined digest.",
        cited_urls=tuple(video["url"] for video in videos),
    )
    model.with_structured_output.side_effect = [summary_model, digest_model]
    return (
        discovery_tool,
        transcript_tool,
        acknowledgement_tool,
        model,
        summary_model,
        digest_model,
    )


def test_youtube_catch_up_processes_a_bounded_queue_sequentially() -> None:
    """One run summarizes only its total budget and combines those summaries."""
    videos = [make_video(1), make_video(2), make_video(3)]
    discovery, transcripts, acknowledgement, model, summary_model, digest_model = (
        make_dependencies(
            videos=videos,
            digest=YouTubeCatchUpAnswer(
                answer="The first two videos cover related work.",
                cited_urls=(videos[0]["url"], videos[1]["url"]),
            ),
        )
    )
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    result = asyncio.run(graph.ainvoke({"days": 7, "max_videos": 2}))

    assert [video["video_id"] for video in result["videos"]] == [
        "video-1",
        "video-2",
    ]
    assert result["cited_urls"] == [videos[0]["url"], videos[1]["url"]]
    assert result["caveats"] == [
        "Discovery caveat.",
        "Transcript truncated for Video 2.",
    ]
    discovery_call = discovery.ainvoke.await_args.args[0]
    assert discovery_call["args"] == {"include_processed": False, "days": 7}
    assert [call.args[0]["args"] for call in transcripts.ainvoke.await_args_list] == [
        {"url": videos[0]["url"], "include_segments": False},
        {"url": videos[1]["url"], "include_segments": False},
    ]
    assert summary_model.ainvoke.await_count == 2
    assert digest_model.ainvoke.await_count == 1
    acknowledgement_call = acknowledgement.ainvoke.await_args.args[0]
    assert acknowledgement_call["args"] == {
        "transcript_call_ids": ["transcript-call-1", "transcript-call-2"]
    }

    digest_messages = digest_model.ainvoke.await_args.args[0]
    digest_input = json.loads(digest_messages[1][1])
    assert [video["summary"] for video in digest_input["videos"]] == [
        "Summary 1",
        "Summary 2",
    ]
    assert "transcript" not in digest_input["videos"][0]


def test_youtube_digest_survives_a_failed_acknowledgement() -> None:
    """A validated digest is never discarded because acknowledgement failed."""
    videos = [make_video(1)]
    discovery, transcripts, acknowledgement, model, _, _ = make_dependencies(
        videos=videos,
        digest=YouTubeCatchUpAnswer(
            answer="One video covered new work.",
            cited_urls=(videos[0]["url"],),
        ),
    )
    acknowledgement.ainvoke = AsyncMock(side_effect=RuntimeError("Net-Razor is gone"))
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    result = asyncio.run(graph.ainvoke({"max_videos": 1}))

    assert result["answer"] == "One video covered new work."
    assert result["cited_urls"] == [videos[0]["url"]]
    assert any("may appear again" in caveat for caveat in result["caveats"])


def test_youtube_preparation_returns_receipts_without_acknowledging() -> None:
    """A caller receives completion receipts without triggering the side effect."""
    video = make_video(1)
    discovery, transcripts, acknowledgement, model, _, _ = make_dependencies(
        videos=[video]
    )
    graph = create_youtube_catch_up_preparation_graph(
        discovery,
        transcripts,
        model,
    )

    result = asyncio.run(graph.ainvoke({"days": 7, "max_videos": 1}))

    assert result["transcript_call_ids"] == ["transcript-call-1"]
    assert result["answer"] == "Combined digest."
    acknowledgement.ainvoke.assert_not_awaited()


def test_youtube_catch_up_skips_model_calls_for_an_empty_queue() -> None:
    """No discovered videos produces a deterministic empty result."""
    discovery, transcripts, acknowledgement, model, summary_model, digest_model = (
        make_dependencies(videos=[])
    )
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    result = asyncio.run(graph.ainvoke({}))

    assert result == {
        "answer": "No new YouTube videos were found.",
        "cited_urls": [],
        "videos": [],
        "caveats": [],
    }
    transcripts.ainvoke.assert_not_awaited()
    summary_model.ainvoke.assert_not_awaited()
    digest_model.ainvoke.assert_not_awaited()
    acknowledgement.ainvoke.assert_not_awaited()


def test_youtube_catch_up_reports_an_unavailable_transcript() -> None:
    """A provider-reported transcript problem does not become model evidence."""
    video = make_video(1)
    discovery, transcripts, acknowledgement, model, summary_model, digest_model = (
        make_dependencies(
            videos=[video],
            transcript_results=[
                {
                    "text": "",
                    "truncated": False,
                    "errors": [{"type": "transcripts_disabled"}],
                }
            ],
        )
    )
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    result = asyncio.run(graph.ainvoke({}))

    assert result["answer"] == "No usable YouTube transcripts were available."
    assert result["videos"] == []
    assert result["caveats"] == [
        "Discovery caveat.",
        "Transcript unavailable for Video 1.",
    ]
    summary_model.ainvoke.assert_not_awaited()
    digest_model.ainvoke.assert_not_awaited()
    acknowledgement.ainvoke.assert_not_awaited()


def test_youtube_catch_up_rejects_an_invalid_total_budget() -> None:
    """An invalid ORIS model-work budget fails before discovery."""
    discovery, transcripts, acknowledgement, model, summary_model, digest_model = (
        make_dependencies(videos=[])
    )
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    with pytest.raises(ValueError, match="max_videos must be between"):
        asyncio.run(graph.ainvoke({"max_videos": 11}))

    discovery.ainvoke.assert_not_awaited()
    transcripts.ainvoke.assert_not_awaited()
    summary_model.ainvoke.assert_not_awaited()
    digest_model.ainvoke.assert_not_awaited()
    acknowledgement.ainvoke.assert_not_awaited()


def test_youtube_catch_up_rejects_an_unavailable_citation() -> None:
    """The final digest cannot cite a video absent from its summaries."""
    video = make_video(1)
    discovery, transcripts, acknowledgement, model, _, _ = make_dependencies(
        videos=[video],
        digest=YouTubeCatchUpAnswer(
            answer="Unsupported claim.",
            cited_urls=("https://www.youtube.com/watch?v=not-summarized",),
        ),
    )
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    with pytest.raises(ValueError, match="cited unavailable URLs"):
        asyncio.run(graph.ainvoke({}))

    acknowledgement.ainvoke.assert_not_awaited()


def test_youtube_catch_up_does_not_acknowledge_a_failed_digest() -> None:
    """A synthesis failure leaves successfully fetched videos discoverable."""
    video = make_video(1)
    discovery, transcripts, acknowledgement, model, _, digest_model = make_dependencies(
        videos=[video]
    )
    digest_model.ainvoke.side_effect = RuntimeError("digest failed")
    graph = create_youtube_catch_up_graph(
        discovery, transcripts, acknowledgement, model
    )

    with pytest.raises(RuntimeError, match="digest failed"):
        asyncio.run(graph.ainvoke({}))

    acknowledgement.ainvoke.assert_not_awaited()
