"""Tests for the local searchable knowledge repository."""

from datetime import UTC, datetime

import pytest

from oris.knowledge import KnowledgeDocument, KnowledgeRepository


def make_document(
    document_id: str,
    content: str,
    *,
    source_type: str = "chat",
) -> KnowledgeDocument:
    """Create a small valid document for repository tests."""
    return KnowledgeDocument(
        document_id=document_id,
        source_type=source_type,
        source_ref="main" if source_type == "chat" else "artifacts/report.md",
        created_at=datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
        title=f"Document {document_id}",
        content=content,
    )


def test_knowledge_repository_search_survives_reopen(tmp_path) -> None:
    """A new repository instance finds content written by an earlier one."""
    database_path = tmp_path / "knowledge.sqlite"
    KnowledgeRepository(database_path).add(
        make_document("chat-1", "The remembered code word is cobalt-731.")
    )

    matches = KnowledgeRepository(database_path).search("cobalt")

    assert [match.document_id for match in matches] == ["chat-1"]


def test_knowledge_repository_filters_by_source_type(tmp_path) -> None:
    """Callers can restrict retrieval to chat or scheduled reports."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    repository.add(make_document("chat-1", "LangGraph persistence notes."))
    repository.add(
        make_document(
            "report-1",
            "A scheduled LangGraph research report.",
            source_type="scheduled_run",
        )
    )

    matches = repository.search("LangGraph", source_type="scheduled_run")

    assert [match.document_id for match in matches] == ["report-1"]


def test_knowledge_repository_replaces_a_document_by_id(tmp_path) -> None:
    """Re-indexing the same source does not leave stale duplicate content."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    repository.add(make_document("chat-1", "The old topic was cobalt."))
    repository.add(make_document("chat-1", "The new topic is amber."))

    assert repository.search("cobalt") == ()
    assert [match.document_id for match in repository.search("amber")] == ["chat-1"]


def test_knowledge_repository_bounds_results(tmp_path) -> None:
    """The caller controls a small, explicit retrieval limit."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    for number in range(3):
        repository.add(make_document(f"chat-{number}", "A shared research topic."))

    assert len(repository.search("research", limit=2)) == 2


def test_knowledge_repository_can_put_newest_matches_first(tmp_path) -> None:
    """An explicit newest plan orders matching documents by creation time."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    older = make_document("older", "A shared scheduled report.").model_copy(
        update={"created_at": datetime(2026, 7, 27, 12, 0, tzinfo=UTC)}
    )
    newer = make_document("newer", "A shared scheduled report.").model_copy(
        update={"created_at": datetime(2026, 7, 28, 12, 0, tzinfo=UTC)}
    )
    repository.add(older)
    repository.add(newer)

    matches = repository.search("shared", sort_order="newest")

    assert [match.document_id for match in matches] == ["newer", "older"]


@pytest.mark.parametrize("query", ["", "   ", "?!"])
def test_knowledge_repository_rejects_empty_search_terms(tmp_path, query) -> None:
    """Punctuation-only or blank searches cannot query the entire archive."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")

    with pytest.raises(ValueError, match="searchable text"):
        repository.search(query)


def test_deleting_by_source_ref_removes_only_that_source(tmp_path) -> None:
    """Deleting a conversation has to reach its answers, and stop there."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    for index, thread in enumerate(["doomed", "doomed", "kept"]):
        repository.add(
            KnowledgeDocument(
                document_id=f"document-{index}",
                source_type="chat",
                source_ref=thread,
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
                title=f"question {index}",
                content=f"an answer about canary tokens {index}",
            )
        )

    assert repository.count_by_source_ref("doomed") == 2
    assert repository.delete_by_source_ref("doomed") == 2
    assert repository.count_by_source_ref("doomed") == 0
    assert [document.source_ref for document in repository.search("canary")] == ["kept"]


def test_deleting_an_unknown_source_ref_removes_nothing(tmp_path) -> None:
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")

    assert repository.delete_by_source_ref("never-existed") == 0


def test_constructing_a_repository_touches_no_disk(tmp_path) -> None:
    """The composition root builds one at import time.

    So creating a database here meant that merely importing that module — to
    read a setting, to collect a test, to list the graphs for the development
    server — wrote a directory and a file, wherever the process resolved the
    path to. Nothing should exist until something actually stores or searches.
    """
    database_path = tmp_path / "archive" / "knowledge.sqlite"

    repository = KnowledgeRepository(database_path)

    assert not database_path.parent.exists()
    assert repository.count_by_source_ref("session-1") == 0
    assert database_path.is_file()


def test_an_archived_exchange_keeps_both_halves_of_the_turn(tmp_path) -> None:
    """A recall answer has to be findable by what was asked as well as answered.

    Both interfaces archive through this, so the shape is fixed in one place.
    The request is the title because that is what a later search is phrased
    like, and both halves are in the content because a question is often the
    only searchable text in an exchange whose answer is a table or JSON.
    """
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")

    archived = repository.add_exchange(
        thread_id="session-1",
        request="What scheduler did we choose?",
        answer="APScheduler, configured by schedules.toml.",
        selected_mode="chat",
    )

    assert archived is True
    document = repository.search("scheduler")[0]
    assert document.source_type == "chat"
    assert document.source_ref == "session-1"
    assert document.title == "What scheduler did we choose?"
    assert document.content == (
        "User:\nWhat scheduler did we choose?\n\n"
        "ORIS:\nAPScheduler, configured by schedules.toml."
    )


def test_a_recall_answer_is_not_archived_back_into_the_archive(tmp_path) -> None:
    """It is a derived copy of documents the archive already holds.

    Archiving it would let `/recall` find its own earlier output and cite that
    instead of the original, compounding every time the question is asked.
    """
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")

    archived = repository.add_exchange(
        thread_id="session-1",
        request="What scheduler did we choose?",
        answer="We chose APScheduler [1].",
        selected_mode="local_knowledge",
    )

    assert archived is False
    assert repository.count_by_source_ref("session-1") == 0


def test_search_matches_a_different_word_form(tmp_path) -> None:
    """Retrieval matches on word form, not on the exact token that was stored.

    A person asking about "schedules" means the same thing as a report that
    says "scheduled", and an archive that answers one but not the other is
    not usable. Without stemming a real archive returned nothing for the word
    the user typed, then filled the gap with unrelated documents that happened
    to share a common word.
    """
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    repository.add(
        make_document("1", "The scheduled research job runs each weekday morning.")
    )

    for query in ("schedules", "scheduling", "schedule"):
        documents = repository.search(query)
        assert [document.document_id for document in documents] == ["1"], query


def test_an_archive_built_without_stemming_is_rebuilt(tmp_path) -> None:
    """An existing archive is re-indexed rather than left on the old tokenizer.

    The table is created with IF NOT EXISTS, so changing the tokenizer alone
    leaves every archive that already exists on the old one — silently, with
    the code claiming one behaviour and the data providing another.
    """
    database_path = tmp_path / "knowledge.sqlite"
    repository = KnowledgeRepository(database_path)
    repository.add(make_document("1", "The scheduled research job ran."))

    _rebuild_without_stemming(database_path)

    documents = KnowledgeRepository(database_path).search("schedules")
    assert [document.document_id for document in documents] == ["1"]


def _rebuild_without_stemming(database_path) -> None:
    """Recreate the archive the way it was built before stemming was added."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT document_id, source_type, source_ref, created_at, title, content"
            " FROM knowledge_documents"
        ).fetchall()
        connection.execute("DROP TABLE knowledge_documents")
        connection.execute(
            """
            CREATE VIRTUAL TABLE knowledge_documents USING fts5(
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
        connection.executemany(
            "INSERT INTO knowledge_documents VALUES (?, ?, ?, ?, ?, ?)", rows
        )
        connection.commit()
