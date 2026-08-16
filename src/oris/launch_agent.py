"""Render and manage the transitional macOS LaunchAgents for ORIS services."""

import argparse
import os
import subprocess
from dataclasses import dataclass
from html import escape
from pathlib import Path
from string import Template

LAUNCHCTL = Path("/bin/launchctl")
PLUTIL = Path("/usr/bin/plutil")

# Every service is named, launched, and logged the same way: one label built
# from its name, one absolute executable inside the project's own virtual
# environment, one pair of log files named after it. A service that needed its
# own convention would be a service whose paths could drift from the rest.
SERVICES = ("scheduler", "phoenix")
EXECUTABLES = {"scheduler": "oris-scheduler", "phoenix": "oris-phoenix"}


def label_for(service: str) -> str:
    """Return the launchd label for one ORIS service."""
    return f"com.rppalmer.oris.{service}"


@dataclass(frozen=True)
class LaunchAgentPaths:
    """Resolved project and user paths used by the LaunchAgent."""

    service: str
    label: str
    project_root: Path
    template: Path
    rendered: Path
    installed: Path
    executable: Path
    schedule_file: Path
    log_directory: Path

    @classmethod
    def from_project_root(
        cls,
        project_root: Path,
        service: str = "scheduler",
    ) -> "LaunchAgentPaths":
        """Resolve every path for one service from one explicit project root."""
        root = project_root.expanduser().resolve()
        label = label_for(service)
        filename = f"{label}.plist"
        return cls(
            service=service,
            label=label,
            project_root=root,
            template=(root / "launchd" / f"{label}.plist.template"),
            rendered=root / "artifacts" / "launchd" / filename,
            installed=Path.home() / "Library" / "LaunchAgents" / filename,
            executable=root / ".venv" / "bin" / EXECUTABLES[service],
            schedule_file=root / "schedules.toml",
            log_directory=root / "logs",
        )


def render_plist(paths: LaunchAgentPaths) -> bool:
    """Render a valid machine-specific plist and report whether it changed."""
    if not paths.executable.is_file():
        raise FileNotFoundError(f"Service executable not found: {paths.executable}")
    if paths.service == "scheduler" and not paths.schedule_file.is_file():
        raise FileNotFoundError(f"Schedule file not found: {paths.schedule_file}")

    substitutions = {
        "label": escape(paths.label),
        "executable": escape(str(paths.executable)),
        "project_root": escape(str(paths.project_root)),
        "stdout_path": escape(str(paths.log_directory / f"{paths.service}.stdout.log")),
        "stderr_path": escape(str(paths.log_directory / f"{paths.service}.stderr.log")),
    }
    if paths.service == "scheduler":
        substitutions["schedule_file"] = escape(str(paths.schedule_file))

    template = Template(paths.template.read_text(encoding="utf-8"))
    content = template.substitute(substitutions)

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


def service_target(label: str) -> str:
    """Return the current user's launchd GUI service target."""
    return f"gui/{os.getuid()}/{label}"


def domain_target() -> str:
    """Return the current user's launchd GUI domain target."""
    return f"gui/{os.getuid()}"


def is_loaded(label: str) -> bool:
    """Return whether launchd currently knows this service."""
    result = subprocess.run(
        [str(LAUNCHCTL), "print", service_target(label)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def start(paths: LaunchAgentPaths) -> None:
    """Load an installed LaunchAgent if it is not already running."""
    if not paths.installed.is_file():
        raise FileNotFoundError(f"LaunchAgent is not installed: {paths.installed}")
    if not is_loaded(paths.label):
        subprocess.run(
            [str(LAUNCHCTL), "bootstrap", domain_target(), str(paths.installed)],
            check=True,
        )


def stop(paths: LaunchAgentPaths) -> None:
    """Unload the LaunchAgent if it is currently running."""
    if is_loaded(paths.label):
        subprocess.run(
            [str(LAUNCHCTL), "bootout", service_target(paths.label)],
            check=True,
        )


def install(paths: LaunchAgentPaths) -> None:
    """Render, install, and bootstrap the current user's LaunchAgent."""
    render_plist(paths)
    validate_plist(paths.rendered)

    stop(paths)

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
    stop(paths)
    paths.installed.unlink(missing_ok=True)


def restart(paths: LaunchAgentPaths) -> None:
    """Start an installed service or restart the loaded service."""
    if not paths.installed.is_file():
        raise FileNotFoundError(f"LaunchAgent is not installed: {paths.installed}")
    if is_loaded(paths.label):
        subprocess.run(
            [str(LAUNCHCTL), "kickstart", "-k", service_target(paths.label)],
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
    if not is_loaded(paths.label):
        print(f"Installed but not loaded: {paths.installed}")
        return 1
    return subprocess.run(
        [str(LAUNCHCTL), "print", service_target(paths.label)],
        check=False,
    ).returncode


def main() -> None:
    """Manage ORIS services."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "service",
        choices=SERVICES,
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
    paths = LaunchAgentPaths.from_project_root(args.project_root, args.service)

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
        print(f"Started: {paths.label}")
    elif args.action == "stop":
        stop(paths)
        print(f"Stopped: {paths.label}")
    elif args.action == "restart":
        restart(paths)
        print(f"Restarted: {paths.label}")
    else:
        raise SystemExit(status(paths))
