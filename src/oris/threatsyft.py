"""Official MCP connection for the local ThreatSyft capability provider."""

from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

ENRICHMENT_SERVER_NAME = "threatsyft_enrichment"
KNOWLEDGE_SERVER_NAME = "threatsyft_knowledge"

# Only the tools the Threat Intel specialist actually calls. The status tools
# each server also exposes are diagnostics and stay out of the allowlist.
THREAT_INTEL_TOOL_NAMES = (
    "extract_iocs",
    "enrich",
    "lookup",
    "search",
)


def create_threatsyft_client(
    python_executable: Path,
    project_root: Path,
) -> MultiServerMCPClient:
    """Configure the official stateless stdio client for both ThreatSyft servers.

    ThreatSyft is a src-layout project that is not installed into its own
    virtual environment, so it is launched from its project root with `src` on
    `PYTHONPATH`, matching the server's own documented invocation.
    """
    if not python_executable.is_absolute():
        raise ValueError("THREATSYFT_PYTHON_EXECUTABLE must be an absolute path")
    if not python_executable.is_file():
        raise FileNotFoundError(
            f"ThreatSyft Python executable not found: {python_executable}"
        )
    if not project_root.is_absolute():
        raise ValueError("THREATSYFT_ROOT must be an absolute path")
    if not project_root.is_dir():
        raise FileNotFoundError(f"ThreatSyft project root not found: {project_root}")

    def connection(module: str) -> dict[str, object]:
        return {
            "transport": "stdio",
            "command": str(python_executable),
            "args": ["-m", module],
            "cwd": str(project_root),
            "env": {"PYTHONPATH": str(project_root / "src")},
        }

    return MultiServerMCPClient(
        {
            ENRICHMENT_SERVER_NAME: connection("threatsyft.mcp.enrichment_server"),
            KNOWLEDGE_SERVER_NAME: connection("threatsyft.mcp.knowledge_server"),
        },
        handle_tool_errors=False,
    )


async def load_threat_intel_tools(
    python_executable: Path,
    project_root: Path,
) -> tuple[BaseTool, ...]:
    """Load only the MCP tools approved for Threat Intel, in a fixed order."""
    client = create_threatsyft_client(python_executable, project_root)
    available_tools = await client.get_tools()
    tools_by_name = {tool.name: tool for tool in available_tools}
    missing_tools = [
        name for name in THREAT_INTEL_TOOL_NAMES if name not in tools_by_name
    ]
    if missing_tools:
        raise RuntimeError(
            f"ThreatSyft is missing required MCP tools: {', '.join(missing_tools)}"
        )
    return tuple(tools_by_name[name] for name in THREAT_INTEL_TOOL_NAMES)
