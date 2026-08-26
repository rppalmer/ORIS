"""Tests for the fixed Podcast Catch-up graph.

Deliberately shares no fixtures with the YouTube tests. Podcast Catch-up is a
candidate replacement for that specialist rather than a sibling, and removing
YouTube should be a deletion rather than an untangling.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from oris.podcast_catch_up import (
    PodcastCatchUpAnswer,
    TranscriptSummary,
    create_podcast_catch_up_graph,
    create_podcast_catch_up_preparation_graph,
)
from oris.threat_reports import ThreatReportStore


def make_episode(number: int) -> dict:
    """One Net-Razor discovery item, in its generic evidence shape.

    The field names matter and are not the obvious ones: the transcript tools
    take an episode ID and a feed URL, which arrive here as `source_id` and
    `query_used`.
    """
    return {
        "source": "podcast",
        "source_backend": "podcast-rss",
        "source_id": f"episode-{number}",
        "item_type": "episode",
        "canonical_url": f"https://example.com/episode-{number}",
        "title": f"Episode {number}",
        "text": "The publisher's description.",
        "author": {
            "handle": "https://feeds.example.com/show.xml",
            "display_name": "Example Show",
        },
        "published_at": f"2026-08-0{number}T12:00:00+00:00",
        "query_used": "https://feeds.example.com/show.xml",
    }


def tool_message(tool_name: str, content: dict) -> ToolMessage:
    """Wrap structured MCP content as the official adapter does."""
    return ToolMessage(
        content="Net-Razor returned structured data.",
        artifact={"structured_content": content},
        tool_call_id=f"{tool_name}-call",
        name=tool_name,
    )


def transcript_page(
    call_id: str,
    text: str,
    *,
    backend: str = "publisher",
    part: int = 1,
    part_count: int = 1,
    next_offset: int | None = None,
) -> dict:
    """One successful transcript response, shaped as Net-Razor sends it."""
    return {
        "call_id": call_id,
        "source_backend": backend,
        "text": text,
        "truncated": part_count > 1,
        "part": part,
        "part_count": part_count,
        "next_offset": next_offset,
        "from_cache": False,
        "errors": [],
    }


def transcript_error(error_type: str, *, retriable: bool = False) -> dict:
    """One handled transcript failure, carrying Net-Razor's classification."""
    return {
        "call_id": "error-call",
        "source_backend": "publisher",
        "text": None,
        "truncated": False,
        "part": 0,
        "part_count": 0,
        "next_offset": None,
        "from_cache": False,
        "errors": [
            {
                "type": error_type,
                "message": f"Net-Razor reported {error_type}.",
                "retriable": retriable,
            }
        ],
    }


def make_tools(*, episodes: list[dict], transcript_pages: list[dict]) -> dict:
    """Create controlled doubles for the podcast tools."""
    discovery_tool = Mock(spec=BaseTool)
    discovery_tool.name = "net_razor_podcast_new_episodes"
    discovery_tool.ainvoke = AsyncMock(
        return_value=tool_message(
            discovery_tool.name, {"items": episodes, "errors": []}
        )
    )

    transcript_tool = Mock(spec=BaseTool)
    transcript_tool.name = "net_razor_podcast_transcript"
    transcript_tool.ainvoke = AsyncMock(
        side_effect=[
            tool_message(transcript_tool.name, page) for page in transcript_pages
        ]
    )

    transcription_tool = Mock(spec=BaseTool)
    transcription_tool.name = "net_razor_podcast_whisper_transcript"

    acknowledgement_tool = Mock(spec=BaseTool)
    acknowledgement_tool.name = "net_razor_podcast_mark_processed"
    acknowledgement_tool.ainvoke = AsyncMock(
        return_value=tool_message(acknowledgement_tool.name, {"errors": []})
    )
    return {
        "discovery": discovery_tool,
        "transcript": transcript_tool,
        "transcription": transcription_tool,
        "acknowledgement": acknowledgement_tool,
    }


def make_model(*, summaries: int = 1) -> Mock:
    """A model double returning fixed structured summaries and one digest."""
    model = Mock(spec=BaseChatModel)
    summary_model = AsyncMock()
    summary_model.ainvoke.side_effect = [
        TranscriptSummary(summary=f"Summary {number}")
        for number in range(1, summaries + 2)
    ]
    digest_model = AsyncMock()
    digest_model.ainvoke.return_value = PodcastCatchUpAnswer(
        answer="Combined digest.",
        cited_urls=("https://example.com/episode-1",),
    )
    model.with_structured_output.side_effect = [summary_model, digest_model]
    return model


def transcription_args(tool: Mock) -> list[dict]:
    """The arguments every transcription call was made with."""
    return [call.args[0]["args"] for call in tool.ainvoke.await_args_list]


def test_a_published_transcript_is_never_replaced_by_transcription() -> None:
    """An episode with a publisher transcript is never sent to Whisper.

    Net-Razor's store is first-writer-wins, so transcribing an episode whose
    publisher transcript was never fetched forecloses that better version
    permanently. The published one usually identifies who is speaking and
    machine transcription never does, so the ordering is not an optimisation.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "Published words."),
            transcript_page("call-1", "Published words."),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({}))

    tools["transcription"].ainvoke.assert_not_awaited()
    assert result["episodes"][0]["transcript_backend"] == "publisher"


def test_transcription_runs_only_when_no_transcript_was_published() -> None:
    """`no_transcript_found` is the one error that leads to transcription."""
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_error("no_transcript_found"),
            transcript_page("call-1", "Machine words.", backend="whisper"),
        ],
    )
    tools["transcription"].ainvoke = AsyncMock(
        return_value=tool_message(
            tools["transcription"].name,
            transcript_page("whisper-call", "Machine words.", backend="whisper"),
        )
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({}))

    assert transcription_args(tools["transcription"]) == [
        {
            "episode_id": "episode-1",
            "feed_url": "https://feeds.example.com/show.xml",
            "offset": 0,
        }
    ]
    assert result["episodes"][0]["transcript_backend"] == "whisper"


@pytest.mark.parametrize(
    "error_type", ["not_configured", "whisper_unavailable", "audio_unavailable"]
)
def test_any_other_error_is_a_caveat_rather_than_a_transcription(
    error_type: str,
) -> None:
    """Only Net-Razor's own classification decides, and only one value acts."""
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[transcript_error(error_type)],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({}))

    tools["transcription"].ainvoke.assert_not_awaited()
    assert result["episodes"] == []
    assert any(error_type in caveat for caveat in result["caveats"])


def test_a_later_page_failing_does_not_reach_for_transcription() -> None:
    """The transcription decision is made on the first page and only there.

    Page one succeeding means a transcript exists. Falling back to Whisper for
    a later page would trade a published transcript away to recover one page.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "Opening words.", part_count=2, next_offset=100),
            transcript_page("call-1", "Opening words.", part_count=2, next_offset=100),
            transcript_error("request_failed"),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(summaries=2),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({}))

    tools["transcription"].ainvoke.assert_not_awaited()
    assert result["episodes"][0]["transcript_backend"] == "publisher"
    assert any("incomplete" in caveat for caveat in result["caveats"])


def test_without_transcription_a_missing_transcript_is_only_a_caveat() -> None:
    """The interactive graph degrades rather than blocking on a slow tool.

    It is built without the transcription tool at all, which is also what
    happens on any path where Net-Razor has transcription switched off.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[transcript_error("no_transcript_found")],
    )
    graph = create_podcast_catch_up_graph(
        tools["discovery"],
        tools["transcript"],
        tools["acknowledgement"],
        make_model(),
    )

    result = asyncio.run(graph.ainvoke({}))

    assert result["episodes"] == []
    assert any("publishes no transcript" in caveat for caveat in result["caveats"])
    tools["acknowledgement"].ainvoke.assert_not_awaited()


def test_the_run_budget_bounds_the_queue_before_any_transcript_call() -> None:
    """Net-Razor caps per feed; how much one run costs is orchestration's call."""
    tools = make_tools(
        episodes=[make_episode(number) for number in (1, 2, 3)],
        transcript_pages=[
            transcript_page("call-1", "First words."),
            transcript_page("call-1", "First words."),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({"max_episodes": 1}))

    assert [episode["episode_id"] for episode in result["episodes"]] == ["episode-1"]
    assert tools["transcript"].ainvoke.await_count == 2


def test_an_out_of_range_budget_is_refused_before_any_external_call() -> None:
    """The budget is checked before Net-Razor is contacted at all."""
    tools = make_tools(episodes=[make_episode(1)], transcript_pages=[])
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
    )

    with pytest.raises(ValueError, match="max_episodes must be between 1 and 10"):
        asyncio.run(graph.ainvoke({"max_episodes": 11}))

    tools["discovery"].ainvoke.assert_not_awaited()


def test_a_machine_transcribed_episode_says_so_in_its_caveats() -> None:
    """A digest that cannot tell repeats Whisper's mistakes as fact.

    Whisper gets names, acronyms, and version numbers wrong, which is precisely
    the detail an investigation repeats and attributes to the episode.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_error("no_transcript_found"),
            transcript_page("call-1", "Machine words.", backend="whisper"),
        ],
    )
    tools["transcription"].ainvoke = AsyncMock(
        return_value=tool_message(
            tools["transcription"].name,
            transcript_page("whisper-call", "Machine words.", backend="whisper"),
        )
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({}))

    assert any("machine-transcribed" in caveat for caveat in result["caveats"])


def test_the_show_and_episode_come_from_net_razors_own_fields() -> None:
    """Titles, shows, and URLs come from the provider, not from the model."""
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "Words."),
            transcript_page("call-1", "Words."),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
    )

    episode = asyncio.run(graph.ainvoke({}))["episodes"][0]

    assert episode["show"] == "Example Show"
    assert episode["title"] == "Episode 1"
    assert episode["url"] == "https://example.com/episode-1"
    assert episode["episode_id"] == "episode-1"


def test_acknowledgement_passes_receipts_under_the_name_net_razor_expects() -> None:
    """The acknowledgement argument is `call_ids`, not YouTube's name for it."""
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("receipt-1", "Words."),
            transcript_page("receipt-1", "Words."),
        ],
    )
    graph = create_podcast_catch_up_graph(
        tools["discovery"],
        tools["transcript"],
        tools["acknowledgement"],
        make_model(),
    )

    asyncio.run(graph.ainvoke({}))

    acknowledgement = tools["acknowledgement"].ainvoke.await_args.args[0]
    assert acknowledgement["args"] == {"call_ids": ["receipt-1"]}


def test_a_feed_that_could_not_be_read_is_reported() -> None:
    """A run covering six of eight feeds must not look like one covering all eight."""
    tools = make_tools(episodes=[make_episode(1)], transcript_pages=[])
    tools["discovery"].ainvoke = AsyncMock(
        return_value=tool_message(
            tools["discovery"].name,
            {
                "items": [],
                "errors": [
                    {
                        "type": "feed_unavailable",
                        "message": "Could not read https://feeds.example.com/gone.xml",
                        "retriable": True,
                    }
                ],
            },
        )
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
    )

    result = asyncio.run(graph.ainvoke({}))

    assert any("gone.xml" in caveat for caveat in result["caveats"])


def test_the_scheduled_report_says_where_each_transcript_came_from() -> None:
    """A reader who cannot tell weighs a mangled name as heavily as a written one."""
    from uuid import UUID

    from oris.scheduled_runs import _format_podcast_catch_up_report
    from oris.schedules import PodcastCatchUpScheduledJob

    job = PodcastCatchUpScheduledJob(
        id="nightly-podcasts",
        enabled=True,
        cron="0 6 * * *",
        task="podcast_catch_up",
        days=1,
        max_episodes=5,
    )
    result = {
        "answer": "Two shows covered the same release.",
        "cited_urls": ["https://example.com/episode-1"],
        "episodes": [
            {
                "episode_id": "episode-1",
                "title": "Episode 1",
                "show": "Example Show",
                "published_at": "2026-08-01T12:00:00+00:00",
                "url": "https://example.com/episode-1",
                "summary": "Summary 1",
                "transcript_backend": "whisper",
                "transcript_truncated": False,
            }
        ],
        "caveats": ["Episode 1 was machine-transcribed."],
        "transcript_call_ids": ["receipt-1"],
    }

    report = _format_podcast_catch_up_report(job, UUID(int=1), result)

    assert "- Transcript: `whisper`, `complete`" in report
    assert "machine-transcribed" in report
    assert "receipt-1" not in report


def test_an_uncited_digest_survives_as_a_caveat() -> None:
    """A finished digest is not thrown away for citing nothing.

    Unlike Web Research, where an uncited claim is unverifiable because the
    sources exist nowhere else, the podcast report lists every episode and its
    canonical URL in its own section. So an uncited digest is still traceable,
    and failing the run would cost a whole night's digest to protect something
    the report already provides. Observed against the real feeds: the model
    wrote a good cross-cutting digest and cited nothing, and the run died.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "Words."),
            transcript_page("call-1", "Words."),
        ],
    )
    model = make_model()
    uncited = PodcastCatchUpAnswer(answer="A digest that cites nothing.", cited_urls=())
    model.with_structured_output.side_effect = None
    summary_model = AsyncMock()
    summary_model.ainvoke.side_effect = [TranscriptSummary(summary="Summary 1")]
    digest_model = AsyncMock()
    digest_model.ainvoke.return_value = uncited
    model.with_structured_output.side_effect = [summary_model, digest_model]

    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], model
    )

    result = asyncio.run(graph.ainvoke({}))

    assert "A digest that cites nothing." in result["answer"]
    assert result["cited_urls"] == []
    assert any("cites no episode" in caveat for caveat in result["caveats"])


def test_a_digest_citing_something_never_supplied_still_fails() -> None:
    """Inventing a URL is fabrication and stays fatal."""
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "Words."),
            transcript_page("call-1", "Words."),
        ],
    )
    model = make_model()
    summary_model = AsyncMock()
    summary_model.ainvoke.side_effect = [TranscriptSummary(summary="Summary 1")]
    digest_model = AsyncMock()
    digest_model.ainvoke.return_value = PodcastCatchUpAnswer(
        answer="A digest.", cited_urls=("https://example.com/never-supplied",)
    )
    model.with_structured_output.side_effect = [summary_model, digest_model]

    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], model
    )

    with pytest.raises(ValueError, match="cited unavailable URLs"):
        asyncio.run(graph.ainvoke({}))


def make_episode_for(feed: str, show: str, number: int) -> dict:
    """One discovery item belonging to a named feed."""
    episode = make_episode(number)
    episode["query_used"] = feed
    episode["author"] = {"handle": feed, "display_name": show}
    episode["source_id"] = f"{show}-{number}"
    return episode


def test_a_prolific_feed_cannot_crowd_out_the_others() -> None:
    """The budget is spread across feeds, not spent on whoever posts most.

    Observed against the real feeds: a daily show took six of eight slots and
    the weekly shows never appeared. Net-Razor caps per feed, and taking the
    newest N globally threw that fairness away — raising the budget only admits
    more of the same show before reaching anyone else.
    """
    episodes = [
        make_episode_for("feed-daily", "Daily Show", 1),
        make_episode_for("feed-daily", "Daily Show", 2),
        make_episode_for("feed-daily", "Daily Show", 3),
        make_episode_for("feed-weekly", "Weekly Show", 1),
        make_episode_for("feed-rare", "Rare Show", 1),
    ]
    tools = make_tools(episodes=episodes, transcript_pages=[])
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], make_model()
    )

    assert _selected(graph, tools, max_episodes=3) == [
        "Daily Show-1",
        "Weekly Show-1",
        "Rare Show-1",
    ]


def test_round_robin_falls_back_to_a_feed_with_more_left() -> None:
    """A budget larger than the number of feeds keeps going round."""
    episodes = [
        make_episode_for("feed-daily", "Daily Show", 1),
        make_episode_for("feed-daily", "Daily Show", 2),
        make_episode_for("feed-daily", "Daily Show", 3),
        make_episode_for("feed-weekly", "Weekly Show", 1),
    ]
    tools = make_tools(episodes=episodes, transcript_pages=[])
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], make_model()
    )

    assert _selected(graph, tools, max_episodes=4) == [
        "Daily Show-1",
        "Weekly Show-1",
        "Daily Show-2",
        "Daily Show-3",
    ]


def _selected(graph, tools, *, max_episodes: int) -> list[str]:
    """The episode IDs the graph chose to work on, in order."""
    from oris.podcast_catch_up import select_episodes

    discovered = tools["discovery"].ainvoke.return_value.artifact["structured_content"][
        "items"
    ]
    return [
        episode["source_id"] for episode in select_episodes(discovered, max_episodes)
    ]


def test_naming_a_show_returns_its_newest_episode_alone() -> None:
    """Asking about one show summarises one episode, not a whole catch-up.

    The show is matched on the display name Net-Razor already returns, so ORIS
    never needs to know a feed URL. That keeps the boundary the contract sets:
    narrowing to a configured show is not the same as supplying an arbitrary
    feed.
    """
    episodes = [
        make_episode_for("feed-a", "Daily Show", 1),
        make_episode_for("feed-b", "LINUX Unplugged", 1),
        make_episode_for("feed-b", "LINUX Unplugged", 2),
    ]
    tools = make_tools(
        episodes=episodes,
        transcript_pages=[
            transcript_page("call-1", "Words."),
            transcript_page("call-1", "Words."),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], make_model()
    )

    result = asyncio.run(graph.ainvoke({"show": "linux unplugged"}))

    assert [e["episode_id"] for e in result["episodes"]] == ["LINUX Unplugged-1"]


def test_a_show_nobody_follows_says_so_without_calling_the_model() -> None:
    """A name that matches nothing is answered plainly, not with an empty run."""
    tools = make_tools(
        episodes=[make_episode_for("feed-a", "Daily Show", 1)],
        transcript_pages=[],
    )
    model = make_model()
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], model
    )

    result = asyncio.run(graph.ainvoke({"show": "gardeners question time"}))

    assert result["episodes"] == []
    assert "gardeners question time" in result["answer"]
    tools["transcript"].ainvoke.assert_not_awaited()


def test_a_long_episode_is_read_to_its_end() -> None:
    """Reading stops when the transcript stops, not at a fixed page count.

    A per-episode page cap punished exactly one thing — a long episode — while
    short ones left the budget unused. It cut the same weekly show short twice,
    at six pages and again at eight, and prevented nothing.
    """
    pages = [
        transcript_page(
            "call-1", f"Part {n}.", part=n, part_count=10, next_offset=n * 100
        )
        for n in range(1, 10)
    ]
    pages.append(transcript_page("call-1", "Part 10.", part=10, part_count=10))
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[transcript_page("call-1", "Part 1.")] + pages,
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], make_model(summaries=10)
    )

    result = asyncio.run(graph.ainvoke({}))

    assert result["episodes"][0]["transcript_truncated"] is False
    assert not any("truncated" in c.lower() for c in result["caveats"])


def test_the_run_stops_reading_once_its_whole_budget_is_spent() -> None:
    """The ceiling is what one run can afford, which is a run-level fact.

    It still exists, because the alternative guard is the summarising node's
    timeout, and that kills the entire digest rather than trimming one episode.
    """
    from oris.podcast_catch_up import MAX_TRANSCRIPT_PARTS_PER_RUN

    endless = [
        transcript_page(
            "call-1", f"Part {n}.", part=n, part_count=99, next_offset=n * 100
        )
        for n in range(1, MAX_TRANSCRIPT_PARTS_PER_RUN + 5)
    ]
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[transcript_page("call-1", "Part 1.", next_offset=100)]
        + endless,
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(summaries=MAX_TRANSCRIPT_PARTS_PER_RUN + 5),
    )

    result = asyncio.run(graph.ainvoke({}))

    assert result["episodes"][0]["transcript_truncated"] is True
    assert any("truncated" in c.lower() for c in result["caveats"])


def test_naming_a_show_transcribes_it_in_chat() -> None:
    """Chat has the transcription tool, and a named show is allowed to use it.

    Most of the shows in real use publish no transcript. A chat that could
    never transcribe answered "nothing to summarise" for exactly the shows
    someone would bother naming, which made the feature useless where it was
    meant to be used.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_error("no_transcript_found"),
            transcript_page("call-1", "Machine words.", backend="whisper"),
        ],
    )
    tools["transcription"].ainvoke = AsyncMock(
        return_value=tool_message(
            tools["transcription"].name,
            transcript_page("whisper-call", "Machine words.", backend="whisper"),
        )
    )
    graph = create_podcast_catch_up_graph(
        tools["discovery"],
        tools["transcript"],
        tools["acknowledgement"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({"show": "Example Show"}))

    tools["transcription"].ainvoke.assert_awaited_once()
    assert result["episodes"][0]["transcript_backend"] == "whisper"


def test_a_chat_catch_up_never_starts_a_transcription() -> None:
    """The same graph holds the tool and refuses to use it for a whole run.

    A catch-up can queue five episodes, and transcribing them one after another
    would hold the interface for the better part of an hour. Naming a show caps
    the run at one episode, which is the difference the rule turns on.
    """
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[transcript_error("no_transcript_found")],
    )
    tools["transcription"].ainvoke = AsyncMock()
    graph = create_podcast_catch_up_graph(
        tools["discovery"],
        tools["transcript"],
        tools["acknowledgement"],
        make_model(),
        transcription_tool=tools["transcription"],
    )

    result = asyncio.run(graph.ainvoke({}))

    tools["transcription"].ainvoke.assert_not_awaited()
    assert result["episodes"] == []
    assert any("Ask for this show by name" in caveat for caveat in result["caveats"])


def test_the_scheduled_run_still_transcribes_a_whole_catch_up() -> None:
    """Nobody is waiting on the scheduled run, so its budget is the only bound."""
    tools = make_tools(
        episodes=[make_episode(1), make_episode(2)],
        transcript_pages=[
            transcript_error("no_transcript_found"),
            transcript_error("no_transcript_found"),
            transcript_page("call-1", "Machine words.", backend="whisper"),
            transcript_page("call-2", "More machine words.", backend="whisper"),
        ],
    )
    tools["transcription"].ainvoke = AsyncMock(
        side_effect=[
            tool_message(
                tools["transcription"].name,
                transcript_page(
                    f"whisper-{number}", "Machine words.", backend="whisper"
                ),
            )
            for number in (1, 2)
        ]
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(summaries=2),
        transcription_tool=tools["transcription"],
    )

    asyncio.run(graph.ainvoke({}))

    assert tools["transcription"].ainvoke.await_count == 2


def test_a_run_stores_the_transcripts_its_summaries_were_made_from(
    tmp_path: Path,
) -> None:
    """A summary always prompts the question a summary cannot answer.

    Threat Intel stores full provider responses for exactly this reason, and
    podcasts now use the same store, so both are opened the same way from the
    same key.
    """
    store = ThreatReportStore(tmp_path / "reports", retention_days=30)
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "First half. "),
            transcript_page("call-1", "First half. "),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        report_store=store,
    )

    asyncio.run(graph.ainvoke({"thread_id": "thread-7"}))

    stored = store.latest()
    assert stored is not None
    assert stored["thread_id"] == "thread-7"
    episode = stored["evidence"]["episodes"][0]
    assert episode["transcript"] == "First half. "
    assert episode["transcript_backend"] == "publisher"
    assert episode["title"] == "Episode 1"


def test_a_run_with_no_usable_transcript_stores_nothing(tmp_path: Path) -> None:
    """An empty evidence file would claim a run was recorded when it was not."""
    store = ThreatReportStore(tmp_path / "reports", retention_days=30)
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[transcript_error("no_transcript_found")],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(),
        report_store=store,
    )

    asyncio.run(graph.ainvoke({}))

    assert store.latest() is None


def test_the_whole_transcript_is_stored_not_only_what_was_summarised(
    tmp_path: Path,
) -> None:
    """Every page read is kept, so a long episode is complete in the evidence."""
    store = ThreatReportStore(tmp_path / "reports", retention_days=30)
    tools = make_tools(
        episodes=[make_episode(1)],
        transcript_pages=[
            transcript_page("call-1", "Part one. ", next_offset=1),
            transcript_page("call-1", "Part one. ", next_offset=1),
            transcript_page("call-1", "Part two.", next_offset=None),
        ],
    )
    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"],
        tools["transcript"],
        make_model(summaries=2),
        report_store=store,
    )

    asyncio.run(graph.ainvoke({}))

    stored = store.latest()
    assert stored is not None
    assert stored["evidence"]["episodes"][0]["transcript"] == "Part one. Part two."


def test_each_show_is_summarised_on_its_own(tmp_path: Path) -> None:
    """A busy show cannot crowd a quiet one out of the digest.

    One digest across every feed let the biggest subject win: the feeds are not
    one subject, and asking for agreements and connections across basketball,
    politics and Linux produced a digest about whichever show published most
    that week while the rest went unmentioned. Sectioning is not formatting —
    a show cannot be crowded out of a call that only contains it.
    """
    tools = make_tools(
        episodes=[
            make_episode_for("https://feeds.example.com/hoops.xml", "Hoops Daily", 1),
            make_episode_for("https://feeds.example.com/hoops.xml", "Hoops Daily", 2),
            make_episode_for("https://feeds.example.com/politics.xml", "The Brief", 3),
        ],
        transcript_pages=[
            transcript_page(f"call-{n}", "Words.") for n in (1, 1, 2, 2, 3, 3)
        ],
    )

    model = Mock(spec=BaseChatModel)
    summary_model = AsyncMock()
    summary_model.ainvoke.side_effect = [
        TranscriptSummary(summary=f"Summary {n}") for n in range(1, 5)
    ]
    digest_model = AsyncMock()
    digest_model.ainvoke.side_effect = [
        PodcastCatchUpAnswer(
            answer="Two games covered.",
            cited_urls=("https://example.com/episode-1",),
        ),
        PodcastCatchUpAnswer(
            answer="One bill covered.",
            cited_urls=("https://example.com/episode-3",),
        ),
    ]
    model.with_structured_output.side_effect = [summary_model, digest_model]

    graph = create_podcast_catch_up_preparation_graph(
        tools["discovery"], tools["transcript"], model
    )
    result = asyncio.run(graph.ainvoke({"max_episodes": 3}))

    # One call per show, each shown only its own episodes.
    assert digest_model.ainvoke.await_count == 2
    supplied = [
        json.loads(call.args[0][1][1]) for call in digest_model.ainvoke.await_args_list
    ]
    assert [payload["show"] for payload in supplied] == ["Hoops Daily", "The Brief"]
    assert {episode["show"] for episode in supplied[0]["episodes"]} == {"Hoops Daily"}
    assert {episode["show"] for episode in supplied[1]["episodes"]} == {"The Brief"}

    assert "## Hoops Daily" in result["answer"]
    assert "## The Brief" in result["answer"]
    assert "One bill covered." in result["answer"]
    assert result["cited_urls"] == [
        "https://example.com/episode-1",
        "https://example.com/episode-3",
    ]
