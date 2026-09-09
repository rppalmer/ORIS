"""Tests for the LaunchDaemon management behind `orisctl`."""

from pathlib import Path

import pytest

from oris import launch_agent
from oris.launch_agent import (
    LAUNCH_DAEMONS,
    LaunchAgentPaths,
    domain_target,
    label_for,
    service_target,
)

SERVICE_ACTIONS = (
    launch_agent.start,
    launch_agent.stop,
    launch_agent.restart,
    launch_agent.install,
    launch_agent.uninstall,
)


@pytest.fixture
def paths(tmp_path: Path) -> LaunchAgentPaths:
    return LaunchAgentPaths.from_project_root(
        tmp_path, "scheduler", user="bot", group="staff"
    )


@pytest.mark.parametrize("action", SERVICE_ACTIONS, ids=lambda f: f.__name__)
def test_every_action_that_changes_the_job_refuses_without_root(
    action, paths, monkeypatch
) -> None:
    """Bootstrapping, booting out and kickstarting all address the system domain.

    Only `install` and `uninstall` write the plist, but all five change a job
    that belongs to root, so all five have to say so rather than letting
    `launchctl` fail with a called-process traceback.
    """
    monkeypatch.setattr(launch_agent.os, "geteuid", lambda: 501)
    ran: list[list[str]] = []
    monkeypatch.setattr(
        launch_agent.subprocess, "run", lambda command, **_: ran.append(command)
    )

    with pytest.raises(PermissionError) as failure:
        action(paths)

    assert "Re-run with sudo" in str(failure.value)
    assert ran == [], "launchctl must not be reached before the check"


@pytest.mark.parametrize("action", SERVICE_ACTIONS, ids=lambda f: f.__name__)
def test_the_refusal_separates_the_domain_from_the_service_account(
    action, paths, monkeypatch
) -> None:
    """A service running as an unprivileged account still needs root to manage.

    This is the question the message exists to answer: the plist's `UserName`
    decides who the process runs as, and the domain decides who may change the
    job. Reading the first as the second is why the sudo looks unnecessary.
    """
    monkeypatch.setattr(launch_agent.os, "geteuid", lambda: 501)

    with pytest.raises(PermissionError) as failure:
        action(paths)

    message = str(failure.value)
    assert "system domain" in message
    assert "unprivileged account" in message


def test_the_daemon_is_addressed_in_the_system_domain() -> None:
    """Not `gui/<uid>`, which exists only while its user is logged in."""
    assert domain_target() == "system"
    assert (
        service_target(label_for("scheduler")) == "system/com.rppalmer.oris.scheduler"
    )


def test_managing_a_service_needs_neither_the_checkout_nor_the_account(
    tmp_path: Path,
) -> None:
    """What `restart` reads is fixed, which is why the command takes no arguments.

    The installed plist is named from the label alone and lives in a fixed
    directory, so a restart cannot be told the wrong checkout or the wrong
    service account.
    """
    from_one = LaunchAgentPaths.from_project_root(tmp_path, "scheduler", user="bot")
    from_other = LaunchAgentPaths.from_project_root(
        tmp_path / "elsewhere", "scheduler", user="someone"
    )

    assert from_one.installed == from_other.installed
    assert from_one.installed.parent == LAUNCH_DAEMONS
    assert from_one.label == from_other.label
