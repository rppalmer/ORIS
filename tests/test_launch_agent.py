"""Tests for rendering and managing the ORIS service LaunchAgents."""

import grp
import os
import plistlib
import pwd
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import call, patch

import pytest

from oris.launch_agent import (
    EXECUTABLES,
    LAUNCHCTL,
    SERVICES,
    LaunchAgentPaths,
    default_user,
    domain_target,
    install,
    label_for,
    main,
    primary_group,
    render_plist,
    restart,
    service_target,
    start,
    status,
    stop,
    uninstall,
)


def _fake_project(tmp_path: Path, service: str) -> LaunchAgentPaths:
    """Create a complete fake project without touching the user LaunchAgents."""
    project_root = tmp_path / "project"
    template_name = f"{label_for(service)}.plist.template"
    source_template = Path(__file__).parents[1] / "launchd" / template_name
    template = project_root / "launchd" / template_name
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(source_template.read_text(encoding="utf-8"), encoding="utf-8")

    executable = project_root / ".venv" / "bin" / EXECUTABLES[service]
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("test executable", encoding="utf-8")
    (project_root / "schedules.toml").write_text(
        'timezone = "America/Detroit"\n',
        encoding="utf-8",
    )

    paths = LaunchAgentPaths.from_project_root(
        project_root, service, user="bot", group="staff"
    )
    # Installed somewhere writable: the real destination is
    # /Library/LaunchDaemons, which a test must never touch.
    return replace(paths, installed=tmp_path / "daemons" / paths.installed.name)


@pytest.fixture
def launch_agent_paths(tmp_path: Path) -> LaunchAgentPaths:
    """The scheduler service, which every lifecycle test drives."""
    return _fake_project(tmp_path, "scheduler")


def test_render_plist_is_explicit_and_idempotent(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Rendering produces one stable plist without embedding secrets."""
    assert render_plist(launch_agent_paths) is True
    assert render_plist(launch_agent_paths) is False

    values = plistlib.loads(launch_agent_paths.rendered.read_bytes())
    assert values == {
        "Label": launch_agent_paths.label,
        "ProgramArguments": [
            str(launch_agent_paths.executable),
            "--schedule-file",
            str(launch_agent_paths.schedule_file),
        ],
        "WorkingDirectory": str(launch_agent_paths.project_root),
        "UserName": "bot",
        "GroupName": "staff",
        "RunAtLoad": True,
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


@patch("oris.launch_agent.os.geteuid", return_value=0)
def test_install_replaces_only_the_loaded_scheduler_service(
    _geteuid,
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
            [str(LAUNCHCTL), "bootout", service_target(launch_agent_paths.label)],
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


@patch("oris.launch_agent.os.geteuid", return_value=0)
def test_uninstall_is_safe_when_the_service_is_already_absent(
    _geteuid,
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


def test_stop_boots_out_the_loaded_service(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Stop unloads only the named service, never the other one."""
    with (
        patch("oris.launch_agent.is_loaded", return_value=True),
        patch("oris.launch_agent.subprocess.run") as run,
    ):
        stop(launch_agent_paths)

    run.assert_called_once_with(
        [str(LAUNCHCTL), "bootout", service_target(launch_agent_paths.label)],
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
        [str(LAUNCHCTL), "kickstart", "-k", service_target(launch_agent_paths.label)],
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
        [str(LAUNCHCTL), "print", service_target(launch_agent_paths.label)],
        check=False,
    )


def test_orisctl_dispatches_scheduler_stop() -> None:
    """The public command requires a service before its lifecycle action."""
    with (
        patch("sys.argv", ["orisctl", "scheduler", "stop"]),
        patch("oris.launch_agent.stop") as stop_command,
    ):
        main()

    assert stop_command.call_args.args[0].label == label_for("scheduler")


def test_every_service_follows_the_same_path_rules(tmp_path: Path) -> None:
    """No service gets its own convention for where things live.

    The scheduler is launched from an absolute path inside the project's own
    virtual environment. Phoenix used to be launched by a shell script that
    called `uvx` off PATH and restated the trace directory in its own words —
    two mechanisms, and a LaunchAgent's minimal PATH breaks the second one.
    Both now render the same way, so the rule is checked rather than remembered.
    """
    rendered = {}
    for service in SERVICES:
        paths = _fake_project(tmp_path / service, service)
        render_plist(paths)
        rendered[service] = plistlib.loads(paths.rendered.read_bytes())

    for service, values in rendered.items():
        program = Path(values["ProgramArguments"][0])
        assert program.is_absolute()
        assert program.parent.name == "bin"
        assert program.parent.parent.name == ".venv"
        assert program.name == EXECUTABLES[service]
        assert values["Label"] == label_for(service)
        assert Path(values["StandardOutPath"]).name == f"{service}.stdout.log"
        assert Path(values["StandardErrorPath"]).name == f"{service}.stderr.log"
        # Credentials and settings stay out of launchd; ORIS reads its own.
        assert values["EnvironmentVariables"] == {"PYTHONUNBUFFERED": "1"}

    labels = {values["Label"] for values in rendered.values()}
    assert len(labels) == len(SERVICES)


def test_daemon_runs_at_boot_as_a_named_unprivileged_user(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """The two properties this exists for, checked in the rendered plist.

    `RunAtLoad` is what removes the login: a daemon that only has `KeepAlive`
    is restarted when it dies, not started when the machine boots. `UserName`
    is what keeps it unprivileged — launchd runs a daemon as root otherwise,
    and the whole point of running under `bot` is that it does not.
    """
    paths = replace(launch_agent_paths, user="bot", group="staff")
    render_plist(paths)

    values = plistlib.loads(paths.rendered.read_bytes())

    assert values["RunAtLoad"] is True
    assert values["UserName"] == "bot"
    assert values["GroupName"] == "staff"


def test_daemon_installs_system_wide_rather_than_per_user(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """A per-user LaunchAgent is exactly the login dependency being removed."""
    paths = LaunchAgentPaths.from_project_root(launch_agent_paths.project_root)

    assert paths.installed.parent == Path("/Library/LaunchDaemons")


def test_daemon_targets_the_system_domain(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """`gui/<uid>` only exists while that user is logged in."""
    assert domain_target() == "system"
    assert (
        service_target(launch_agent_paths.label) == f"system/{launch_agent_paths.label}"
    )


def test_install_refuses_without_root_rather_than_failing_late(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """Writing to /Library/LaunchDaemons needs root; say so before doing work.

    Half-installing and then failing on `launchctl bootstrap` leaves a plist
    on disk that nothing loaded, which reads exactly like a working install.
    """
    with (
        patch("oris.launch_agent.os.geteuid", return_value=501),
        pytest.raises(PermissionError, match="sudo"),
    ):
        install(replace(launch_agent_paths, user="bot", group="staff"))


def test_install_requires_a_user_to_run_as(
    launch_agent_paths: LaunchAgentPaths,
) -> None:
    """A daemon with no UserName runs as root, which is never what is wanted."""
    paths = replace(launch_agent_paths, user="", group="")
    with (
        patch("oris.launch_agent.os.geteuid", return_value=0),
        pytest.raises(ValueError, match="user"),
    ):
        install(paths)


def test_default_user_is_the_invoking_account_under_sudo() -> None:
    """`sudo orisctl install` means that person's account, never root."""
    with patch.dict("os.environ", {"SUDO_USER": "bot"}):
        assert default_user() == "bot"


def test_primary_group_is_looked_up_rather_than_assumed() -> None:
    """Hard-coding "staff" would be wrong on any account that is not."""
    user = pwd.getpwuid(os.getuid()).pw_name

    assert primary_group(user) == grp.getgrgid(pwd.getpwuid(os.getuid()).pw_gid).gr_name
