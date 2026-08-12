"""Render and manage the transitional macOS scheduler LaunchAgent."""

import argparse
import os
import subprocess
from dataclasses import dataclass
from html import escape
from pathlib import Path
from string import Template

LAUNCH_AGENT_LABEL = "com.rppalmer.oris.scheduler"
LAUNCHCTL = Path("/bin/launchctl")
PLUTIL = Path("/usr/bin/plutil")


@dataclass(frozen=True)
class LaunchAgentPaths:
    """Resolved project and user paths used by the LaunchAgent."""

    project_root: Path
    template: Path
    rendered: Path
    installed: Path
    scheduler_executable: Path
    schedule_file: Path
    log_directory: Path

    @classmethod
    def from_project_root(cls, project_root: Path) -> "LaunchAgentPaths":
        """Resolve every path from one explicit project root."""
        root = project_root.expanduser().resolve()
        filename = f"{LAUNCH_AGENT_LABEL}.plist"
        return cls(
            project_root=root,
            template=(root / "launchd" / f"{LAUNCH_AGENT_LABEL}.plist.template"),
            rendered=root / "artifacts" / "launchd" / filename,
            installed=Path.home() / "Library" / "LaunchAgents" / filename,
            scheduler_executable=root / ".venv" / "bin" / "oris-scheduler",
            schedule_file=root / "schedules.toml",
            log_directory=root / "logs",
        )


def render_plist(paths: LaunchAgentPaths) -> bool:
    """Render a valid machine-specific plist and report whether it changed."""
    if not paths.scheduler_executable.is_file():
        raise FileNotFoundError(
            f"Scheduler executable not found: {paths.scheduler_executable}"
        )
    if not paths.schedule_file.is_file():
        raise FileNotFoundError(f"Schedule file not found: {paths.schedule_file}")

    template = Template(paths.template.read_text(encoding="utf-8"))
    content = template.substitute(
        label=escape(LAUNCH_AGENT_LABEL),
        scheduler_executable=escape(str(paths.scheduler_executable)),
        schedule_file=escape(str(paths.schedule_file)),
        project_root=escape(str(paths.project_root)),
        stdout_path=escape(str(paths.log_directory / "scheduler.stdout.log")),
        stderr_path=escape(str(paths.log_directory / "scheduler.stderr.log")),
    )

    if (
        paths.rendered.exists()
        and paths.rendered.read_text(encoding="utf-8") == content
    ):
        return False

    paths.rendered.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = paths.rendered.with_suffix(".plist.tmp")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(paths.rendered)
    finally:
        temporary_path.unlink(missing_ok=True)
    return True


def validate_plist(path: Path) -> None:
    """Use macOS's plist validator before installation."""
    subprocess.run(
        [str(PLUTIL), "-lint", str(path)],
        check=True,
    )


def service_target() -> str:
    """Return the current user's launchd GUI service target."""
    return f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"


def domain_target() -> str:
    """Return the current user's launchd GUI domain target."""
    return f"gui/{os.getuid()}"


def is_loaded() -> bool:
    """Return whether launchd currently knows this service."""
    result = subprocess.run(
        [str(LAUNCHCTL), "print", service_target()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def start(paths: LaunchAgentPaths) -> None:
    """Load an installed LaunchAgent if it is not already running."""
    if not paths.installed.is_file():
        raise FileNotFoundError(f"LaunchAgent is not installed: {paths.installed}")
    if not is_loaded():
        subprocess.run(
            [str(LAUNCHCTL), "bootstrap", domain_target(), str(paths.installed)],
            check=True,
        )


def stop() -> None:
    """Unload the LaunchAgent if it is currently running."""
    if is_loaded():
        subprocess.run(
            [str(LAUNCHCTL), "bootout", service_target()],
            check=True,
        )


def install(paths: LaunchAgentPaths) -> None:
    """Render, install, and bootstrap the current user's LaunchAgent."""
    render_plist(paths)
    validate_plist(paths.rendered)

    stop()

    paths.installed.parent.mkdir(parents=True, exist_ok=True)
    paths.log_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = paths.installed.with_suffix(".plist.tmp")
    try:
        temporary_path.write_bytes(paths.rendered.read_bytes())
        temporary_path.chmod(0o644)
        temporary_path.replace(paths.installed)
    finally:
        temporary_path.unlink(missing_ok=True)

    subprocess.run(
        [str(LAUNCHCTL), "bootstrap", domain_target(), str(paths.installed)],
        check=True,
    )


def uninstall(paths: LaunchAgentPaths) -> None:
    """Boot out and remove only this LaunchAgent's installed plist."""
    stop()
    paths.installed.unlink(missing_ok=True)


def restart(paths: LaunchAgentPaths) -> None:
    """Start an installed service or restart the loaded service."""
    if not paths.installed.is_file():
        raise FileNotFoundError(f"LaunchAgent is not installed: {paths.installed}")
    if is_loaded():
        subprocess.run(
            [str(LAUNCHCTL), "kickstart", "-k", service_target()],
            check=True,
        )
    else:
        subprocess.run(
            [str(LAUNCHCTL), "bootstrap", domain_target(), str(paths.installed)],
            check=True,
        )


def status(paths: LaunchAgentPaths) -> int:
    """Print launchd's service status and return a shell-compatible code."""
    if not paths.installed.is_file():
        print(f"Not installed: {paths.installed}")
        return 1
    if not is_loaded():
        print(f"Installed but not loaded: {paths.installed}")
        return 1
    return subprocess.run(
        [str(LAUNCHCTL), "print", service_target()],
        check=False,
    ).returncode


def main() -> None:
    """Manage ORIS services."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "service",
        choices=("scheduler",),
        help="service to manage",
    )
    parser.add_argument(
        "action",
        choices=(
            "render",
            "install",
            "uninstall",
            "start",
            "stop",
            "restart",
            "status",
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="ORIS project root (default: current directory)",
    )
    args = parser.parse_args()
    paths = LaunchAgentPaths.from_project_root(args.project_root)

    if args.action == "render":
        changed = render_plist(paths)
        validate_plist(paths.rendered)
        state = "Rendered" if changed else "Already current"
        print(f"{state}: {paths.rendered}")
    elif args.action == "install":
        install(paths)
        print(f"Installed and loaded: {paths.installed}")
    elif args.action == "uninstall":
        uninstall(paths)
        print(f"Uninstalled: {paths.installed}")
    elif args.action == "start":
        start(paths)
        print(f"Started: {LAUNCH_AGENT_LABEL}")
    elif args.action == "stop":
        stop()
        print(f"Stopped: {LAUNCH_AGENT_LABEL}")
    elif args.action == "restart":
        restart(paths)
        print(f"Restarted: {LAUNCH_AGENT_LABEL}")
    else:
        raise SystemExit(status(paths))
