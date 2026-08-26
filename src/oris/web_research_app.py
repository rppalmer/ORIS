"""LangGraph development entry point for the Web Research specialist."""

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from oris.chat import LazyMCPSpecialist, create_oris_graph
from oris.community_research import create_community_research_graph
from oris.config import load_settings
from oris.knowledge import KnowledgeRepository
from oris.local_knowledge import create_local_knowledge_graph
from oris.model import create_chat_model
from oris.net_razor import (
    load_community_research_tools,
    load_podcast_catch_up_tools,
    load_podcast_transcription_tool,
    load_youtube_catch_up_tools,
)
from oris.podcast_catch_up import (
    create_podcast_catch_up_graph,
    create_podcast_catch_up_preparation_graph,
)
from oris.tavily import TavilyWebSearch, create_tavily_search
from oris.threat_intel import create_threat_intel_graph
from oris.threat_reports import ThreatReportStore
from oris.threatsyft import load_threat_intel_tools
from oris.web_research import create_web_research_graph
from oris.youtube_catch_up import (
    create_acknowledging_youtube_catch_up_graph,
    create_youtube_catch_up_preparation_graph,
)

settings = load_settings()
if settings.local_tracing_enabled:
    from phoenix.otel import register

    register(
        endpoint=str(settings.phoenix_collector_endpoint),
        project_name="oris",
        batch=True,
        auto_instrument=True,
        verbose=False,
    )

search = TavilyWebSearch(
    general_search=create_tavily_search(settings),
    news_search=create_tavily_search(settings, topic="news"),
)
model = create_chat_model(settings)

web_research_graph = create_web_research_graph(search, model)
knowledge_repository = KnowledgeRepository(settings.knowledge_database_path)
threat_report_store = ThreatReportStore(
    settings.threat_report_directory,
    settings.threat_report_retention_days,
)
local_knowledge_graph = create_local_knowledge_graph(knowledge_repository, model)


async def build_community_research_graph() -> CompiledStateGraph:
    """Load the approved MCP tool and compile Community Research."""
    python_executable = settings.net_razor_python_executable
    if python_executable is None:
        raise ValueError(
            "NET_RAZOR_PYTHON_EXECUTABLE is required for Community Research"
        )
    tools = await load_community_research_tools(python_executable)
    return create_community_research_graph(tools[0], model)


async def build_youtube_catch_up_preparation() -> tuple[CompiledStateGraph, BaseTool]:
    """Build a validated digest graph and return its acknowledgement tool."""
    python_executable = settings.net_razor_python_executable
    if python_executable is None:
        raise ValueError("NET_RAZOR_PYTHON_EXECUTABLE is required for YouTube Catch-up")
    (
        discovery_tool,
        transcript_tool,
        acknowledgement_tool,
    ) = await load_youtube_catch_up_tools(python_executable)
    preparation_graph = create_youtube_catch_up_preparation_graph(
        discovery_tool,
        transcript_tool,
        model,
    )
    return preparation_graph, acknowledgement_tool


async def build_youtube_catch_up_graph() -> CompiledStateGraph:
    """Compile YouTube Catch-up with interactive acknowledgement."""
    preparation_graph, acknowledgement_tool = await build_youtube_catch_up_preparation()
    return create_acknowledging_youtube_catch_up_graph(
        preparation_graph,
        acknowledgement_tool,
    )


def _net_razor_executable() -> Path:
    """The configured Net-Razor interpreter, or a clear reason it is unusable."""
    python_executable = settings.net_razor_python_executable
    if python_executable is None:
        raise ValueError("NET_RAZOR_PYTHON_EXECUTABLE is required for Net-Razor")
    return python_executable


async def build_podcast_catch_up_preparation() -> tuple[CompiledStateGraph, BaseTool]:
    """Build the scheduled podcast graph, which alone holds transcription.

    Transcription arrives from a second MCP client carrying a much longer
    deadline. It is loaded here and nowhere else: the interactive builder below
    never asks for it, so no chat turn can start work that blocks for minutes.
    """
    python_executable = _net_razor_executable()
    (
        discovery_tool,
        transcript_tool,
        acknowledgement_tool,
    ) = await load_podcast_catch_up_tools(python_executable)
    transcription_tool = await load_podcast_transcription_tool(python_executable)
    preparation_graph = create_podcast_catch_up_preparation_graph(
        discovery_tool,
        transcript_tool,
        model,
        transcription_tool=transcription_tool,
    )
    return preparation_graph, acknowledgement_tool


async def build_podcast_catch_up_graph() -> CompiledStateGraph:
    """Compile Podcast Catch-up for chat, where only a named show transcribes."""
    python_executable = _net_razor_executable()
    (
        discovery_tool,
        transcript_tool,
        acknowledgement_tool,
    ) = await load_podcast_catch_up_tools(python_executable)
    transcription_tool = await load_podcast_transcription_tool(python_executable)
    return create_podcast_catch_up_graph(
        discovery_tool,
        transcript_tool,
        acknowledgement_tool,
        model,
        transcription_tool=transcription_tool,
    )


async def build_threat_intel_graph() -> CompiledStateGraph:
    """Load the approved ThreatSyft tools and compile Threat Intel."""
    python_executable = settings.threatsyft_python_executable
    project_root = settings.threatsyft_root
    if python_executable is None or project_root is None:
        raise ValueError(
            "THREATSYFT_PYTHON_EXECUTABLE and THREATSYFT_ROOT are required "
            "for Threat Intel"
        )
    tools = await load_threat_intel_tools(python_executable, project_root)
    return create_threat_intel_graph(*tools, model, report_store=threat_report_store)


async def build_oris_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile ORIS with all currently approved specialists.

    Net-Razor-backed specialists are resolved lazily, so ORIS starts and serves
    direct chat, Web Research, and Local Knowledge without Net-Razor present.
    """
    return create_oris_graph(
        web_research_graph,
        local_knowledge_graph,
        LazyMCPSpecialist(build_community_research_graph),
        LazyMCPSpecialist(build_youtube_catch_up_graph),
        model,
        checkpointer=checkpointer,
        max_history_tokens=settings.local_llm_max_history_tokens,
        threat_intel_graph=LazyMCPSpecialist(build_threat_intel_graph),
        podcast_catch_up_graph=LazyMCPSpecialist(build_podcast_catch_up_graph),
    )


async def make_oris_graph(_config: RunnableConfig) -> CompiledStateGraph:
    """Official LangGraph runtime factory for the complete parent graph."""
    return await build_oris_graph()


async def make_community_research_graph(
    _config: RunnableConfig,
) -> CompiledStateGraph:
    """Official LangGraph runtime factory for direct specialist access."""
    return await build_community_research_graph()
