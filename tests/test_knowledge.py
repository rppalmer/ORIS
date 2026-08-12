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
