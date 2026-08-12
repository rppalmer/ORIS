"""Opt-in end-to-end contract for the Local Knowledge graph."""

import os
from datetime import UTC, datetime

import pytest

from oris.config import Settings
from oris.knowledge import KnowledgeDocument, KnowledgeRepository
from oris.local_knowledge import create_local_knowledge_graph
from oris.model import create_chat_model

LIVE_LOCAL_KNOWLEDGE_ENABLED = (
    os.environ.get("ORIS_RUN_LIVE_LOCAL_KNOWLEDGE_TESTS") == "1"
)


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_LOCAL_KNOWLEDGE_ENABLED,
    reason=("Set ORIS_RUN_LIVE_LOCAL_KNOWLEDGE_TESTS=1 to contact oMLX."),
)
def test_local_knowledge_answers_from_archived_evidence(tmp_path) -> None:
    """The configured oMLX model follows the evidence and citation contract."""
    repository = KnowledgeRepository(tmp_path / "knowledge.sqlite")
    archived_exchange = KnowledgeDocument(
        document_id="known-scheduling-decision",
        source_type="chat",
        source_ref="main",
        created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        title="ORIS scheduling decision",
        content=(
            "User: Which scheduling format should ORIS use?\n\n"
            "ORIS: The project will use schedules.toml as the source "
            "of truth for scheduled jobs."
        ),
    )
    repository.add(archived_exchange)
    settings = Settings(TAVILY_API_KEY="unused-by-local-knowledge-contract")
    model = create_chat_model(settings)
    graph = create_local_knowledge_graph(repository, model)

    result = graph.invoke({"query": "Which scheduling format should ORIS use?"})

    assert result["sources"] == (archived_exchange,)
    assert "schedules.toml" in result["answer"].lower()
    assert "[1]" in result["answer"]
