"""Local full-text search for retained ORIS knowledge."""

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
KnowledgeSource = Literal["chat", "scheduled_run"]
KnowledgeSortOrder = Literal["relevance", "newest"]


def knowledge_search_terms(query: str) -> tuple[str, ...]:
    """Return searchable words or reject a query with no searchable text."""
    terms = tuple(re.findall(r"\w+", query, flags=re.UNICODE))
    if not terms:
        raise ValueError("Knowledge search query must contain searchable text")
    return terms


class KnowledgeDocument(BaseModel):
    """One user-facing chat exchange or scheduled report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: NonEmptyString
    source_type: KnowledgeSource
    source_ref: NonEmptyString
    created_at: AwareDatetime
    title: NonEmptyString
    content: NonEmptyString


class KnowledgeRepository:
    """Persist and search knowledge documents with SQLite full-text search."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        """Open the archive, creating it the first time it is actually used.

        Constructing a repository does not touch the disk. The composition root
        builds one at import, so doing this work in `__init__` meant that
        importing it — to read a setting, to run a test, to list the graphs for
        the development server — created a directory and a database as a side
        effect, in whatever location that process happened to resolve.
        """
        if not self._schema_ready:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_documents USING fts5(
                        document_id UNINDEXED,
                        source_type UNINDEXED,
                        source_ref UNINDEXED,
                        created_at UNINDEXED,
                        title,
                        content,
                        tokenize = 'unicode61'
                    )
                    """
                )
            self._schema_ready = True
        return sqlite3.connect(self.database_path)

    def add(self, document: KnowledgeDocument) -> None:
        """Add a document, replacing an existing document with the same ID."""
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM knowledge_documents WHERE document_id = ?",
                (document.document_id,),
            )
            connection.execute(
                """
                INSERT INTO knowledge_documents(
                    document_id,
                    source_type,
                    source_ref,
                    created_at,
                    title,
                    content
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document.document_id,
                    document.source_type,
                    document.source_ref,
                    document.created_at.astimezone(UTC).isoformat(),
                    document.title,
                    document.content,
                ),
            )
            connection.commit()

    def add_exchange(
        self,
        *,
        thread_id: str,
        request: str,
        answer: str,
        selected_mode: str,
    ) -> bool:
        """Archive one completed interactive turn, and say whether it did.

        Both interfaces have to make the same two decisions here — whether the
        turn belongs in the archive at all, and what shape it takes once it is
        there — and they made them in identical code written twice. A recall
        answer is skipped because it is a derived copy of documents the archive
        already holds; archiving it would let `/recall` find its own output.
        """
        if selected_mode == "local_knowledge":
            return False
        self.add(
            KnowledgeDocument(
                document_id=str(uuid4()),
                source_type="chat",
                source_ref=thread_id,
                created_at=datetime.now(UTC),
                title=request,
                content=f"User:\n{request}\n\nORIS:\n{answer}",
            )
        )
        return True

    def count_by_source_ref(self, source_ref: str) -> int:
        """Count what one conversation or job contributed to the archive.

        Deleting is not recoverable, so the confirmation has to be able to say
        how much is about to go, not just that something will.
        """
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM knowledge_documents WHERE source_ref = ?",
                (source_ref,),
            ).fetchone()
        return int(row[0]) if row else 0

    def delete_by_source_ref(self, source_ref: str) -> int:
        """Remove everything one conversation or job contributed, returning the count.

        Deleting a conversation has to reach its answers too. Left behind, they
        stay searchable through `/recall` under a thread that no longer exists,
        which is not what deleting a conversation means.
        """
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM knowledge_documents WHERE source_ref = ?",
                (source_ref,),
            )
            connection.commit()
            return cursor.rowcount

    def search(
        self,
        query: str,
        *,
        source_type: KnowledgeSource | None = None,
        sort_order: KnowledgeSortOrder = "relevance",
        limit: int = 5,
    ) -> tuple[KnowledgeDocument, ...]:
        """Return the best lexical matches without exposing backend scores."""
        if not 1 <= limit <= 20:
            raise ValueError("Knowledge search limit must be between 1 and 20")
        if source_type not in {None, "chat", "scheduled_run"}:
            raise ValueError(f"Unsupported knowledge source type: {source_type}")
        if sort_order not in {"relevance", "newest"}:
            raise ValueError(f"Unsupported knowledge sort order: {sort_order}")

        terms = knowledge_search_terms(query)
        match_query = " OR ".join(f'"{term}"' for term in terms)

        sql = """
            SELECT
                document_id,
                source_type,
                source_ref,
                created_at,
                title,
                content
            FROM knowledge_documents
            WHERE knowledge_documents MATCH ?
        """
        parameters: list[str | int] = [match_query]
        if source_type is not None:
            sql += " AND source_type = ?"
            parameters.append(source_type)
        if sort_order == "newest":
            sql += " ORDER BY created_at DESC, bm25(knowledge_documents) LIMIT ?"
        else:
            sql += " ORDER BY bm25(knowledge_documents), created_at DESC LIMIT ?"
        parameters.append(limit)

        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, parameters).fetchall()

        return tuple(
            KnowledgeDocument(
                document_id=row["document_id"],
                source_type=row["source_type"],
                source_ref=row["source_ref"],
                created_at=datetime.fromisoformat(row["created_at"]),
                title=row["title"],
                content=row["content"],
            )
            for row in rows
        )
