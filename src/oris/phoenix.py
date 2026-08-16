"""Run the local Phoenix trace collector as an ORIS-owned service.

Phoenix is deliberately not an ORIS dependency. ORIS speaks to it over OTLP and
never imports it, and its dependency tree is large enough that sharing a virtual
environment with LangGraph would be a resolution problem rather than a
convenience. It is installed separately, as a `uv` tool, and pinned there.

What this module owns is the *one* description of how to start it: which port,
which directory, which retention. That used to live in a shell script which
restated `ORIS_HOME`'s default in its own words, with a comment admitting the
two had to be kept in step by hand. Reading the directory from settings means
the collector writes where ORIS looks, by construction.
"""

import os
import shutil
from pathlib import Path

from oris.config import Settings

# Pinned: a collector that silently changes its storage layout under a running
# installation is worse than one that is a few releases behind. The pin is
# applied when the tool is installed, which is why `install_command` exists to
# be quoted back at whoever has not run it.
PHOENIX_PACKAGE = "arize-phoenix==19.6.0"
PHOENIX_HOST = "127.0.0.1"
PHOENIX_PORT = "6006"
RETENTION_DAYS = "14"


def install_command() -> str:
    """Return the command that installs the pinned collector."""
    return f'uv tool install "{PHOENIX_PACKAGE}"'


def phoenix_executable() -> Path:
    """Locate the collector's own console script, by absolute path.

    Deliberately not `uvx`. `uvx` runs the collector as a *child* rather than
    replacing itself, so launchd would supervise the wrapper and the server
    would survive being stopped — measured: a stopped service left the previous
    collector holding port 6006, and the replacement could not bind it. A
    console script installed by `uv tool install` is the server itself, so
    launchd supervises one process and a stop actually stops it.

    A LaunchAgent also inherits a minimal PATH that excludes `~/.local/bin`, so
    resolving through PATH alone would work in every terminal and fail in the
    one place this exists to support. launchd does provide HOME.
    """
    found = shutil.which("phoenix")
    if found is not None:
        return Path(found)
    candidate = Path.home() / ".local" / "bin" / "phoenix"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"The Phoenix collector is not installed. Run: {install_command()}"
    )


def phoenix_environment(settings: Settings) -> dict[str, str]:
    """Return the collector's environment, derived from ORIS's own settings.

    Loopback host, no telemetry, no external resources, and none of Phoenix's
    own model-provider features: this is a local trace viewer, and every one of
    those would widen what a development tool reaches.
    """
    working_directory = settings.phoenix_working_directory
    working_directory.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "PHOENIX_HOST": PHOENIX_HOST,
        "PHOENIX_PORT": PHOENIX_PORT,
        "PHOENIX_WORKING_DIR": str(working_directory),
        "PHOENIX_DEFAULT_RETENTION_POLICY_DAYS": RETENTION_DAYS,
        "PHOENIX_TELEMETRY_ENABLED": "false",
        "PHOENIX_ALLOW_EXTERNAL_RESOURCES": "false",
        "PHOENIX_ENABLE_MCP_SERVER": "false",
        "PHOENIX_ALLOWED_PROVIDERS": "NONE",
        "PHOENIX_ALLOWED_SANDBOX_PROVIDERS": "NONE",
    }


def phoenix_command() -> list[str]:
    """Return the argument vector that starts the collector."""
    return [str(phoenix_executable()), "serve"]


def main() -> None:
    """Run the collector in the foreground until it is stopped.

    Replaces this process rather than supervising it, so that launchd is
    watching the collector and not a wrapper standing in front of it.
    """
    settings = Settings()
    command = phoenix_command()
    os.execve(command[0], command, phoenix_environment(settings))
