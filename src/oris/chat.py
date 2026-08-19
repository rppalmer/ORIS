"""Chat-shaped parent graph for ORIS."""

import json
from collections.abc import Awaitable, Callable
from typing import Literal, NotRequired, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    trim_messages,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import NodeError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from oris.config import DEFAULT_MAX_HISTORY_TOKENS
from oris.prompts import load_system_prompt, with_current_date

ROUTING_SYSTEM_PROMPT = load_system_prompt("routing_system.txt")
DIRECT_CHAT_SYSTEM_PROMPT = load_system_prompt("direct_chat_system.txt")

RouterMode = Literal[
    "chat",
    "community_research",
    "local_knowledge",
    "podcast_catch_up",
    "web_research",
    "youtube_catch_up",
]
"""Destinations the constrained router may choose on its own.

Threat Intel is deliberately absent. Enrichment sends indicators to third-party
providers and consumes paid API credits, so it stays an explicit user choice and
is unreachable from a model decision — the router's output schema cannot express
it. See ADR 001, "External capability boundary".
"""

SelectedMode = RouterMode | Literal["threat_intel"]
RequestMode = SelectedMode | Literal["auto"]

NODE_BY_SELECTED_MODE: dict[str, str] = {
    "chat": "direct_chat",
    "community_research": "community_research",
    "local_knowledge": "local_knowledge",
    "podcast_catch_up": "podcast_catch_up",
    "web_research": "web_research",
    "youtube_catch_up": "youtube_catch_up",
    "threat_intel": "threat_intel",
}

FAILED_COMPONENT_NAMES = {
    "validate_request": "Request validation",
    "route_request": "Request routing",
    "direct_chat": "Direct chat",
    "local_knowledge": "Local Knowledge",
    "web_research": "Web Research",
    "community_research": "Community Research",
    "youtube_catch_up": "YouTube Catch-up",
    "podcast_catch_up": "Podcast Catch-up",
    "threat_intel": "Threat Intel",
}


class AsyncSpecialistGraph(Protocol):
    """A specialist invoked asynchronously, possibly resolved on first use.

    MCP-backed specialists are supplied as lazy proxies so an unavailable
    server degrades one capability instead of preventing ORIS from starting.
    """

    async def ainvoke(self, request: dict, /) -> dict:
        """Run the specialist once and return its public output."""
        ...


class LazyMCPSpecialist:
    """Resolve an MCP-backed specialist on first use, then reuse it.

    Keeps MCP servers out of the ORIS core, as required by ADR 001 "MCP
    independence": the application starts without the server, and a missing or
    failing one fails only its own request through the normal node-failure path
    instead of preventing startup.
    """

    def __init__(self, build: Callable[[], Awaitable[AsyncSpecialistGraph]]) -> None:
        self._build = build
        self._graph: AsyncSpecialistGraph | None = None

    async def ainvoke(self, request: dict, /) -> dict:
        """Build the specialist if it is not resolved yet, then run it once."""
        if self._graph is None:
            self._graph = await self._build()
        return await self._graph.ainvoke(request)


class RoutingDecision(BaseModel):
    """One route and one request prepared for the selected destination."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: RouterMode = Field(
        description="The single fixed ORIS mode that should handle the request."
    )
    resolved_request: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "The latest request prepared for the selected destination using relevant "
            "conversation context without adding new facts. Community Research "
            "receives only its concise search topic."
        ),
    )


REQUEST_FAILURE_MESSAGE = (
    "ORIS could not complete that request. Check that the local model "
    "and required network services are available, then try again."
)


class ORISState(MessagesState):
    """Messages plus an explicit, deterministic request mode."""

    mode: NotRequired[RequestMode]
    selected_mode: NotRequired[SelectedMode]
    resolved_request: NotRequired[str]
    request_succeeded: NotRequired[bool]
    request_error: NotRequired[str | None]


def validate_request(state: ORISState) -> dict:
    """Accept one text request and clear any earlier failure state."""
    messages = state["messages"]
    if not messages or not isinstance(messages[-1], HumanMessage):
        raise ValueError("ORIS requires a human message")
    if not isinstance(messages[-1].content, str):
        raise ValueError("ORIS requires a text message")
    return {
        "resolved_request": messages[-1].content,
        "request_succeeded": True,
        "request_error": None,
    }


def close_failed_request(state: ORISState, error: NodeError) -> dict[str, object]:
    """Remove the failed user turn and retain its error outside messages.

    Registered on every node, so a failure anywhere ends the run without
    adding a synthetic assistant message and without leaving the failed
    request in conversation history.
    """
    reason = str(error.error).strip() or type(error.error).__name__
    component = FAILED_COMPONENT_NAMES.get(error.node, error.node)
    updates: dict[str, object] = {
        "request_succeeded": False,
        "request_error": f"{component} failed: {reason}",
    }
    messages = state["messages"]
    if messages:
        latest_message = messages[-1]
        if isinstance(latest_message, HumanMessage) and latest_message.id is not None:
            updates["messages"] = [RemoveMessage(id=latest_message.id)]
    return updates


def select_mode(state: ORISState) -> str:
    """Return the specialist node that handles the selected mode."""
    return NODE_BY_SELECTED_MODE[state["selected_mode"]]


def _format_source_status(source_status: dict[str, str]) -> str:
    """Render which providers answered as a table the terminal can show.

    A threat lookup is only as good as who actually replied. Without this, a
    missing API key and a clean result read identically in the prose.
    """
    if not source_status:
        return ""
    rows = "\n".join(
        f"| {source} | {'ok' if status == 'ok' else f'**{status}**'} |"
        for source, status in sorted(source_status.items())
    )
    failed = sum(1 for status in source_status.values() if status != "ok")
    heading = f"Sources: {len(source_status) - failed} answered, {failed} failed"
    return f"{heading}\n\n| Source | Result |\n| --- | --- |\n{rows}"


def history_for_model(
    messages: list[BaseMessage],
    max_history_tokens: int,
) -> list[BaseMessage]:
    """Bound the conversation sent to the model without dropping the request.

    Exceeding the model's context window is unrecoverable for a thread: every
    later turn fails the same way. `trim_messages` returns an empty list when
    the newest message alone exceeds the budget, which would leave the model no
    request to answer, so the latest turn is always retained.
    """
    trimmed = trim_messages(
        messages,
        max_tokens=max_history_tokens,
        token_counter="approximate",
        strategy="last",
        start_on="human",
        include_system=False,
    )
    return trimmed or messages[-1:]


async def run_turn(
    graph: CompiledStateGraph,
    request: dict[str, object],
    config: dict[str, object],
    *,
    on_step: Callable[[str], None],
) -> dict:
    """Run one turn, naming each graph node as it starts.

    Streamed rather than invoked so a front end can say what is happening. A
    real `/threat` run measured 29 seconds, 23 of them inside the final model
    call, and one unchanging label for that whole wait cannot tell working
    apart from hung. `subgraphs=True` is what makes the useful steps visible:
    the specialist's own nodes are where the time goes, not the parent's.

    Steps are reported as they *begin*, which is why this reads the debug
    stream rather than node updates — an update arrives when a node finishes,
    which is exactly too late to say what is running. Node names are passed
    through untranslated; wording belongs to the interface.
    """
    final: dict = {}
    async for namespace, mode, chunk in graph.astream(
        request,
        config,
        stream_mode=["values", "debug"],
        subgraphs=True,
        durability="sync",
    ):
        if mode == "debug":
            if chunk.get("type") == "task":
                on_step(chunk["payload"]["name"])
        elif not namespace:
            # Only the parent's values are the turn's result; a subgraph's are
            # its own private state.
            final = chunk
    return final


def create_oris_graph(
    web_research_graph: CompiledStateGraph,
    local_knowledge_graph: CompiledStateGraph,
    community_research_graph: AsyncSpecialistGraph,
    youtube_catch_up_graph: AsyncSpecialistGraph,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    max_history_tokens: int = DEFAULT_MAX_HISTORY_TOKENS,
    threat_intel_graph: AsyncSpecialistGraph | None = None,
    podcast_catch_up_graph: AsyncSpecialistGraph | None = None,
) -> CompiledStateGraph:
    """Create a chat graph with explicit specialist request paths."""
    routing_model = model.with_structured_output(
        RoutingDecision,
        method="json_schema",
    )

    def route_request(state: ORISState) -> dict[str, object]:
        requested_mode = state.get("mode", "chat")
        if requested_mode != "auto":
            return {"selected_mode": requested_mode}

        decision = routing_model.invoke(
            [
                SystemMessage(content=ROUTING_SYSTEM_PROMPT),
                *history_for_model(state["messages"], max_history_tokens),
            ],
            max_completion_tokens=256,
        )
        return {
            "selected_mode": decision.route,
            "resolved_request": decision.resolved_request,
        }

    def run_direct_chat(state: ORISState) -> dict:
        return {
            "messages": [
                model.invoke(
                    [
                        SystemMessage(
                            content=with_current_date(DIRECT_CHAT_SYSTEM_PROMPT)
                        ),
                        *history_for_model(state["messages"], max_history_tokens),
                    ]
                )
            ]
        }

    def run_local_knowledge(
        state: ORISState,
    ) -> dict[str, list[AIMessage]]:
        query = state["resolved_request"]
        result = local_knowledge_graph.invoke({"query": query})
        source_list = "\n".join(
            f"[{number}] {source.title} ({source.source_type}: {source.source_ref})"
            for number, source in enumerate(result["sources"], start=1)
        )
        content = result["answer"]
        if source_list:
            content = f"{content}\n\nArchive sources:\n{source_list}"
        return {"messages": [AIMessage(content=content)]}

    async def run_web_research(
        state: ORISState,
    ) -> dict[str, list[AIMessage]]:
        query = state["resolved_request"]
        result = await web_research_graph.ainvoke({"query": query})
        source_links = "\n".join(
            f"[{number}] [{source.title}]({source.url})"
            for number, source in enumerate(result["sources"], start=1)
        )
        content = f"{result['answer'].answer}\n\nSources:\n{source_links}"
        return {"messages": [AIMessage(content=content)]}

    async def run_community_research(
        state: ORISState,
    ) -> dict[str, list[AIMessage]]:
        query = state["resolved_request"]
        result = await community_research_graph.ainvoke({"topic": query})
        source_links = "\n".join(
            f"[{number}]({url})"
            for number, url in enumerate(result["cited_urls"], start=1)
        )
        content = result["answer"]
        if source_links:
            content = f"{content}\n\nCommunity sources:\n{source_links}"
        return {"messages": [AIMessage(content=content)]}

    async def run_youtube_catch_up(
        _state: ORISState,
    ) -> dict[str, list[AIMessage]]:
        result = await youtube_catch_up_graph.ainvoke({})
        source_links = "\n".join(
            f"[{number}]({url})"
            for number, url in enumerate(result["cited_urls"], start=1)
        )
        caveat_list = "\n".join(f"- {caveat}" for caveat in result["caveats"])
        content = result["answer"]
        if source_links:
            content = f"{content}\n\nYouTube sources:\n{source_links}"
        if caveat_list:
            content = f"{content}\n\nCaveats:\n{caveat_list}"
        return {"messages": [AIMessage(content=content)]}

    async def run_podcast_catch_up(
        _state: ORISState,
    ) -> dict[str, list[AIMessage]]:
        """Answer from the configured feeds, without transcription.

        The graph reached from here never holds the transcription tool, so a
        chat turn cannot start work that blocks for minutes. An episode with no
        published transcript becomes a caveat here and is picked up by the
        scheduled job, which does hold it.
        """
        if podcast_catch_up_graph is None:
            raise ValueError("Podcast Catch-up is not configured")
        result = await podcast_catch_up_graph.ainvoke({})
        source_links = "\n".join(
            f"[{number}]({url})"
            for number, url in enumerate(result["cited_urls"], start=1)
        )
        caveat_list = "\n".join(f"- {caveat}" for caveat in result["caveats"])
        content = result["answer"]
        if source_links:
            content = f"{content}\n\nEpisodes:\n{source_links}"
        if caveat_list:
            content = f"{content}\n\nCaveats:\n{caveat_list}"
        return {"messages": [AIMessage(content=content)]}

    async def run_threat_intel(
        state: ORISState,
        config: RunnableConfig,
    ) -> dict[str, list[AIMessage]]:
        if threat_intel_graph is None:
            raise ValueError(
                "Threat Intel is not configured; set THREATSYFT_PYTHON_EXECUTABLE "
                "and THREATSYFT_ROOT"
            )
        # The only node that needs to know which conversation it is serving:
        # the evidence it stores outlives the turn, and deleting the
        # conversation has to be able to find it again.
        result = await threat_intel_graph.ainvoke(
            {
                "request": state["resolved_request"],
                "thread_id": config.get("configurable", {}).get("thread_id", ""),
            }
        )
        indicator_list = "\n".join(
            f"- {indicator}" for indicator in result["indicators"]
        )
        content = result["answer"]
        if indicator_list:
            content = f"{content}\n\nIndicators examined:\n{indicator_list}"
        # Display detail, so its absence must not discard a finished answer.
        report_id = result.get("report_id")
        if report_id:
            content = (
                f"{content}\n\nFull evidence: `{report_id}` "
                f"— `/threat show {report_id}`"
            )
        report = result.get("report")
        if report:
            # Fenced so the terminal syntax-highlights it and the model reads it
            # back as data rather than prose on a later turn.
            rendered = json.dumps(report, indent=2, ensure_ascii=False)
            content = f"{content}\n\n```json\n{rendered}\n```"
        status_table = _format_source_status(result.get("source_status") or {})
        if status_table:
            content = f"{content}\n\n{status_table}"
        return {"messages": [AIMessage(content=content)]}

    builder = StateGraph(ORISState)
    for node_name, action in (
        ("validate_request", validate_request),
        ("route_request", route_request),
        ("direct_chat", run_direct_chat),
        ("local_knowledge", run_local_knowledge),
        ("web_research", run_web_research),
        ("community_research", run_community_research),
        ("youtube_catch_up", run_youtube_catch_up),
        ("podcast_catch_up", run_podcast_catch_up),
        ("threat_intel", run_threat_intel),
    ):
        builder.add_node(node_name, action, error_handler=close_failed_request)

    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "route_request")
    builder.add_conditional_edges(
        "route_request",
        select_mode,
        sorted(NODE_BY_SELECTED_MODE.values()),
    )
    for specialist_node in sorted(NODE_BY_SELECTED_MODE.values()):
        builder.add_edge(specialist_node, END)
    return builder.compile(checkpointer=checkpointer)
