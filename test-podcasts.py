"""Drive Podcast Catch-up against one feed at a time, then all of them.

Podcast Catch-up takes no feed list — Net-Razor's configured feeds are the
source of truth, and ORIS deliberately cannot narrow them. So testing one show
at a time means pointing Net-Razor at one feed and running the real ORIS graph
against it.

Uses the preparation graph, which returns its receipts without acknowledging
them, so nothing here consumes the queue and every run can be repeated.

    uv run python test-podcasts.py            # each feed alone, then all together
    uv run python test-podcasts.py --solo     # each feed alone only
    uv run python test-podcasts.py --all      # all feeds together only
"""

import asyncio
import shutil
import sys
import time
from pathlib import Path

FEED_FILE = Path.home() / ".net-razor" / "podcasts.txt"
BACKUP = FEED_FILE.with_suffix(".txt.test-backup")


def configured_feeds() -> list[str]:
    lines = FEED_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


async def run_once(label: str, max_episodes: int) -> None:
    """Run the real ORIS graph and report what came back."""
    from oris.web_research_app import build_podcast_catch_up_preparation

    started = time.monotonic()
    try:
        graph, _ = await build_podcast_catch_up_preparation()
        result = await graph.ainvoke({"days": 7, "max_episodes": max_episodes})
    except Exception as error:
        print(f"  FAILED  {type(error).__name__}: {error}")
        return
    elapsed = time.monotonic() - started

    if not result["episodes"]:
        print(f"  no episodes summarised  ({elapsed:.0f}s)")
    for episode in result["episodes"]:
        truncated = " TRUNCATED" if episode["transcript_truncated"] else ""
        print(
            f"  {episode['transcript_backend']:9} {episode['show'][:28]:30}"
            f" {episode['title'][:40]}{truncated}"
        )
    for caveat in result["caveats"]:
        print(f"  caveat: {caveat[:100]}")
    print(f"  {len(result['episodes'])} summarised, {elapsed:.0f}s, "
          f"{len(result['cited_urls'])} cited")


async def main() -> None:
    feeds = configured_feeds()
    solo = "--all" not in sys.argv
    everything = "--solo" not in sys.argv

    if solo:
        shutil.copy(FEED_FILE, BACKUP)
        try:
            for number, feed in enumerate(feeds, start=1):
                print(f"\n[{number}/{len(feeds)}] {feed}")
                FEED_FILE.write_text(f"{feed}\n", encoding="utf-8")
                await run_once(feed, max_episodes=1)
        finally:
            shutil.copy(BACKUP, FEED_FILE)
            BACKUP.unlink(missing_ok=True)
            print(f"\nRestored {len(configured_feeds())} feeds.")

    if everything:
        print(f"\n[all] {len(feeds)} feeds together")
        await run_once("all", max_episodes=5)


if __name__ == "__main__":
    asyncio.run(main())
