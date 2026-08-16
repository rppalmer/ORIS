"""Write real checkpoints, so the session readers are tested against real storage.

Hand-building the rows would test a guess at the checkpointer's format. Putting
them through `SqliteSaver` means a change in how LangGraph serialises state
fails here rather than in front of the user.
"""

from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver


def write_session(
    database_path: Path,
    thread_id: str,
    exchanges: list[tuple[str, str]],
) -> None:
    """Store one conversation as a sequence of checkpoints, one per turn."""
    messages: list[BaseMessage] = []
    with SqliteSaver.from_conn_string(str(database_path)) as saver:
        for index, (request, answer) in enumerate(exchanges):
            messages = [*messages, HumanMessage(request), AIMessage(answer)]
            saver.put(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                {
                    "v": 1,
                    "id": f"{thread_id}-{index}",
                    "ts": f"2026-08-1{index + 1}T10:00:00+00:00",
                    "channel_values": {"messages": messages},
                    "channel_versions": {},
                    "versions_seen": {},
                },
                {"source": "loop", "step": index},
                {},
            )
