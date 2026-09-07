"""Durable record of which retrieved items ORIS has already read.

Net-Razor used to track this and stopped, because it tracked it in one global
table with no notion of who had read what: any consumer's acknowledgement hid
the item from every other consumer. Read state is a fact about a reader rather
than about an item, so it belongs to the reader.

A store of its own with its own SQLite file, following `KnowledgeRepository`
rather than `ThreatReportStore`: what this needs is a keyed set tested for
membership on every run, not files in a directory under a retention window.
The LangGraph checkpointer is wrong for a different reason — checkpoints are
per-thread conversation state, and this has to be global and outlive threads.
"""

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from oris.config import NonEmptyString

ItemSource = Literal["podcast"]
"""What kind of thing was read. Only podcasts today; blog posts are foreseen."""

CREATE_PROCESSED_ITEMS = """
    CREATE TABLE IF NOT EXISTS processed_items(
        source TEXT NOT NULL,
        item_id TEXT NOT NULL,
        call_id TEXT,
        processed_at TEXT NOT NULL,
        PRIMARY KEY (source, item_id)
    )
"""


class ProcessedItem(BaseModel):
    """One item this reader has finished with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: ItemSource
    # Taken straight from `source_id` on the item in the listing response.
    item_id: NonEmptyString
    # The Net-Razor call whose read is being recorded. Kept for traceability
    # only: nothing reads it back, but without it a row cannot be tied to the
    # fetch that justified writing it.
    call_id: str = ""


class ProcessedItemStore:
    """Membership set of what has been read, keyed by source and item."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        """Open the store, creating it the first time it is actually used.

        Constructing one touches no disk, for the same reason the knowledge
        archive does not: the composition root builds it at import, and doing
        this work in `__init__` would create a database as a side effect of
        importing the module to read a setting or list the graphs.
        """
        if not self._schema_ready:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.execute(CREATE_PROCESSED_ITEMS)
            self._schema_ready = True
        return sqlite3.connect(self.database_path)

    def unread(self, source: ItemSource, item_ids: Iterable[str]) -> tuple[str, ...]:
        """Return the given IDs that have not been recorded, in their order.

        Order is preserved because the caller applies its per-run budget to
        this result, and a listing arrives newest first. Returning a set would
        make which episodes a run picks depend on hash ordering.
        """
        wanted = tuple(item_ids)
        if not wanted:
            return ()
        placeholders = ",".join("?" for _ in wanted)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT item_id FROM processed_items "
                f"WHERE source = ? AND item_id IN ({placeholders})",
                (source, *wanted),
            ).fetchall()
        seen = {row[0] for row in rows}
        return tuple(item_id for item_id in wanted if item_id not in seen)

    def record(self, items: Iterable[ProcessedItem]) -> int:
        """Record items as read, ignoring any already recorded.

        Returns how many rows were new. Re-recording is not an error: a run
        that crashed after writing some of its episodes has to be safe to
        repeat, and the whole point of the failure direction here is that an
        item appears again rather than vanishing unread.
        """
        entries = tuple(items)
        if not entries:
            return 0
        processed_at = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            cursor = connection.executemany(
                "INSERT OR IGNORE INTO processed_items("
                "source, item_id, call_id, processed_at) VALUES (?, ?, ?, ?)",
                [
                    (item.source, item.item_id, item.call_id, processed_at)
                    for item in entries
                ],
            )
            connection.commit()
            return cursor.rowcount

    def count(self, source: ItemSource) -> int:
        """How many items of one kind have been read."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM processed_items WHERE source = ?", (source,)
            ).fetchone()
        return int(row[0])
