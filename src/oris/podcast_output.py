"""What a podcast run returns, and how to show it.

Separate from the graph on purpose. Both front ends render this — chat writes
it into a message, the scheduler writes it into a report — and neither of them
should have to import the specialist to do it. The specialist reaches Net-Razor
over MCP, and importing that module pulls the MCP client in behind it; the
terminal interface has a test asserting it does not.

Two copies of a phrase like "transcribed by ORIS earlier" drift until they
contradict each other, so there is one copy and it lives here.
"""

from typing import TypedDict


class PodcastEpisodeSummary(TypedDict):
    """One summarized episode returned in public graph output."""

    episode_id: str
    title: str
    show: str
    published_at: str
    url: str
    summary: str
    transcript_backend: str
    transcript_created_now: bool
    transcript_truncated: bool


def transcript_provenance(episode: PodcastEpisodeSummary) -> str:
    """Say in plain words where one episode's transcript came from.

    Three cases, and the reader needs all three kept apart. A publisher's
    transcript usually identifies who is speaking and gets names right. A
    machine transcript does neither, so a mangled product name in one means
    nothing. And a machine transcript made by an earlier run is a different
    claim from one made just now: it says this run read stored work rather than
    doing any, which is the whole difference between a recap and a catch-up.
    """
    if episode["transcript_backend"] != "whisper":
        return "publisher's transcript"
    if episode["transcript_created_now"]:
        return "transcribed by ORIS during this run"
    return "transcribed by ORIS earlier"


def episode_lines(episodes: list[PodcastEpisodeSummary]) -> str:
    """List every episode the run covered, with its show and its provenance."""
    return "\n".join(
        f"{number}. [{episode['title']}]({episode['url']}) — {episode['show']}, "
        f"{transcript_provenance(episode)}"
        for number, episode in enumerate(episodes, start=1)
    )


class PodcastShow(TypedDict):
    """One configured show, as Net-Razor names it."""

    show_title: str
    episode_count: int
    latest_episode_title: str | None
    latest_episode_at: str | None
    publishes_transcripts: bool


def show_lines(shows: list[PodcastShow]) -> str:
    """List the configured shows, saying which of them need transcribing.

    `publishes_transcripts` is read from each show's newest episode, so it
    describes what the show does now rather than what any particular episode
    has. That is the right thing for someone deciding which show to ask about:
    naming a show that publishes its own transcripts answers in seconds, and
    naming one that does not means waiting for Whisper.
    """
    lines = []
    for number, show in enumerate(shows, start=1):
        transcripts = (
            "publishes transcripts"
            if show["publishes_transcripts"]
            else "needs transcribing"
        )
        newest = show.get("latest_episode_at") or "no episodes"
        lines.append(f"{number}. {show['show_title']} — {transcripts}, newest {newest}")
    return "\n".join(lines)
