"""Tests for rendering and managing the scheduler LaunchAgent."""

import plistlib
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import call, patch

import pytest

from oris.launch_agent import (
    LAUNCH_AGENT_LABEL,
    LAUNCHCTL,
    LaunchAgentPaths,
    domain_target,
    install,
    main,
    render_plist,
    restart,
    service_target,
    start,
    status,
    stop,
    uninstall,
)


@pytest.fixture
def launch_agent_paths(tmp_path: Path) -> LaunchAgentPaths:
    """Create a complete fake project without touching the user LaunchAgents."""
    project_root = tmp_path / "project"
    template_name = f"{LAUNCH_AGENT_LABEL}.plist.template"
    source_template = Path(__file__).parents[1] / "launchd" / template_name
    template = project_root / "launchd" / template_name
    template.parent.mkdir(parents=True)
    template.write_text(source_template.read_text(encoding="utf-8"), encoding="utf-8")

    executable = project_root / ".venv" / "bin" / "oris-scheduler"
    executable.parent.mkdir(parents=True)
    executable.write_text("test executable", encoding="utf-8")
    (project_root / "schedules.toml").write_text(
        'timezone = "America/Detroit"\n',
        encoding="utf-8",
    )

    paths = LaunchAgentPaths.from_project_root(project_root)
    return replace(
        paths,
        installed=tmp_path / "home" / "Library" / "LaunchAgents" / paths.installed.name,
    )


def test_render_plist_is_explicit_and_idempotent(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Rendering produces one stable plist without embedding secrets."""
    assert render_plist(launch_agent_paths) is True
    assert render_plist(launch_agent_paths) is False

    values = plistlib.loads(launch_agent_paths.rendered.read_bytes())
    assert values == {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(launch_agent_paths.scheduler_executable),
            "--schedule-file",
            str(launch_agent_paths.schedule_file),
        ],
        "WorkingDirectory": str(launch_agent_paths.project_root),
        "KeepAlive": True,
        "ProcessType": "Background",
        "Umask": "077",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(
            launch_agent_paths.log_directory / "scheduler.stdout.log"
        ),
        "StandardErrorPath": str(
            launch_agent_paths.log_directory / "scheduler.stderr.log"
        ),
    }
    assert ".env" not in launch_agent_paths.rendered.read_text(encoding="utf-8")


def test_install_replaces_only_the_loaded_scheduler_service(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Installation safely replaces and bootstraps the exact user service."""
    with (
        patch("oris.launch_agent.validate_plist") as validate,
        patch("oris.launch_agent.is_loaded", return_value=True),
        patch("oris.launch_agent.subprocess.run") as run,
    ):
        install(launch_agent_paths)

    validate.assert_called_once_with(launch_agent_paths.rendered)
    assert launch_agent_paths.installed.read_bytes() == (
        launch_agent_paths.rendered.read_bytes()
    )
    assert run.call_args_list == [
        call(
            [str(LAUNCHCTL), "bootout", service_target()],
            check=True,
        ),
        call(
            [
                str(LAUNCHCTL),
                "bootstrap",
                domain_target(),
                str(launch_agent_paths.installed),
            ],
            check=True,
        ),
    ]


def test_uninstall_is_safe_when_the_service_is_already_absent(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Repeated uninstallation removes only the exact plist and stays safe."""
    launch_agent_paths.installed.parent.mkdir(parents=True)
    launch_agent_paths.installed.write_text("installed", encoding="utf-8")

    with (
        patch("oris.launch_agent.is_loaded", return_value=False),
        patch("oris.launch_agent.subprocess.run") as run,
    ):
        uninstall(launch_agent_paths)
        uninstall(launch_agent_paths)

    assert not launch_agent_paths.installed.exists()
    run.assert_not_called()


def test_start_bootstraps_an_installed_stopped_service(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Start loads the installed plist into the current user's domain."""
    launch_agent_paths.installed.parent.mkdir(parents=True)
    launch_agent_paths.installed.write_text("installed", encoding="utf-8")

    with (
        patch("oris.launch_agent.is_loaded", return_value=False),
        patch("oris.launch_agent.subprocess.run") as run,
    ):
        start(launch_agent_paths)

    run.assert_called_once_with(
        [
            str(LAUNCHCTL),
            "bootstrap",
            domain_target(),
            str(launch_agent_paths.installed),
        ],
        check=True,
    )


def test_stop_boots_out_the_loaded_service() -> None:
    """Stop unloads only the ORIS scheduler service."""
    with (
        patch("oris.launch_agent.is_loaded", return_value=True),
        patch("oris.launch_agent.subprocess.run") as run,
    ):
        stop()

    run.assert_called_once_with(
        [str(LAUNCHCTL), "bootout", service_target()],
        check=True,
    )


def test_restart_kickstarts_the_loaded_service(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Restart targets the installed scheduler service and nothing else."""
    launch_agent_paths.installed.parent.mkdir(parents=True)
    launch_agent_paths.installed.write_text("installed", encoding="utf-8")

    with (
        patch("oris.launch_agent.is_loaded", return_value=True),
        patch("oris.launch_agent.subprocess.run") as run,
    ):
        restart(launch_agent_paths)

    run.assert_called_once_with(
        [str(LAUNCHCTL), "kickstart", "-k", service_target()],
        check=True,
    )


def test_status_prints_the_loaded_service(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Status delegates to launchctl print for the exact service target."""
    launch_agent_paths.installed.parent.mkdir(parents=True)
    launch_agent_paths.installed.write_text("installed", encoding="utf-8")
    completed = CompletedProcess(args=[], returncode=0)

    with (
        patch("oris.launch_agent.is_loaded", return_value=True),
        patch("oris.launch_agent.subprocess.run", return_value=completed) as run,
    ):
        result = status(launch_agent_paths)

    assert result == 0
    run.assert_called_once_with(
        [str(LAUNCHCTL), "print", service_target()],
        check=False,
    )


def test_orisctl_dispatches_scheduler_stop() -> None:
    """The public command requires a service before its lifecycle action."""
    with (
        patch("sys.argv", ["orisctl", "scheduler", "stop"]),
        patch("oris.launch_agent.stop") as stop_command,
    ):
        main()

    stop_command.assert_called_once_with()
