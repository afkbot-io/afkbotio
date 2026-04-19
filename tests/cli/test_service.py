"""Tests for managed runtime service CLI commands."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.settings import get_settings
from tests.cli._rendering import invoke_plain_help


def test_service_port_command_persists_runtime_port_and_reloads_service(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service port` should save the runtime port and request a managed reload."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[bool] = []

    monkeypatch.setattr("afkbot.cli.commands.service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.service.ensure_managed_runtime_service",
        lambda settings, *, start: (
            calls.append(start) or SimpleNamespace(status="installed", kind="systemd-user")
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "port", "--runtime-port", "19000"])

    assert result.exit_code == 0
    assert calls == [True]
    config = read_runtime_config(get_settings())
    assert config["runtime_port"] == 19000
    assert "runtime_port=19000" in result.stdout
    get_settings.cache_clear()


def test_service_port_command_rejects_busy_requested_port_without_persisting(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service port` should fail closed before writing a busy bind."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.service.is_runtime_port_pair_available",
        lambda *, host, runtime_port: False,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.service.probe_runtime_stack",
        lambda *, host, runtime_port, api_port=None, timeout_sec=1.0: SimpleNamespace(
            running=False,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "port", "--runtime-port", "19000"])

    assert result.exit_code != 0
    config = read_runtime_config(get_settings())
    assert config == {}
    assert "Requested runtime bind is busy" in result.stderr
    get_settings.cache_clear()


def test_service_port_command_reports_restored_bind_after_reload_failure(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service port` should print the restored bind after rollback, not the failed one."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr("afkbot.cli.commands.service.setup_is_complete", lambda settings: True)
    results = iter(
        [
            SimpleNamespace(status="failed", kind="systemd-user", reason="daemon did not become healthy"),
            SimpleNamespace(status="installed", kind="systemd-user"),
        ]
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.service.ensure_managed_runtime_service",
        lambda settings, *, start: next(results),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "port", "--runtime-port", "19000"])

    assert result.exit_code == 1
    config = read_runtime_config(get_settings())
    assert config == {}
    assert "runtime bind restored: host=127.0.0.1, runtime_port=46339, api_port=46340" in result.stdout
    assert "runtime bind saved: host=127.0.0.1, runtime_port=19000, api_port=19001" not in result.stdout
    get_settings.cache_clear()


def test_service_host_command_persists_runtime_host_and_reloads_service(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service host` should save the runtime host and request a managed reload."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[bool] = []

    monkeypatch.setattr("afkbot.cli.commands.service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.service.ensure_managed_runtime_service",
        lambda settings, *, start: (
            calls.append(start) or SimpleNamespace(status="installed", kind="systemd-user")
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "host", "0.0.0.0"])

    assert result.exit_code == 0
    assert calls == [True]
    config = read_runtime_config(get_settings())
    assert config["runtime_host"] == "0.0.0.0"
    assert "runtime bind saved: host=0.0.0.0" in result.stdout
    get_settings.cache_clear()


def test_service_host_command_allows_switching_running_local_service_to_wildcard_host(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Host changes should be allowed when AFKBOT itself already owns the current port pair."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(
        "afkbot.cli.commands.service.is_runtime_port_pair_available",
        lambda *, host, runtime_port: False,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.service.probe_runtime_stack",
        lambda *, host, runtime_port, api_port=None, timeout_sec=1.0: SimpleNamespace(
            running=True,
        ),
    )
    monkeypatch.setattr("afkbot.cli.commands.service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.service.ensure_managed_runtime_service",
        lambda settings, *, start: SimpleNamespace(status="installed", kind="systemd-user"),
    )
    from afkbot.services.setup.runtime_store import write_runtime_config

    write_runtime_config(
        settings,
        config={"runtime_host": "127.0.0.1", "runtime_port": settings.runtime_port},
    )
    get_settings.cache_clear()
    runner = CliRunner()

    result = runner.invoke(app, ["service", "host", "0.0.0.0"])

    assert result.exit_code == 0
    config = read_runtime_config(get_settings())
    assert config["runtime_host"] == "0.0.0.0"
    assert config["runtime_port"] == settings.runtime_port
    get_settings.cache_clear()


def test_service_port_command_allows_host_then_port_switch_when_wildcard_pair_is_free(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`service host` -> `service port` should succeed when the wildcard bind pair is free."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    new_runtime_port = 19000

    monkeypatch.setattr("afkbot.cli.commands.service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.service.is_runtime_port_pair_available",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.service.probe_runtime_stack",
        lambda **kwargs: SimpleNamespace(running=False),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.service.ensure_managed_runtime_service",
        lambda settings, *, start: SimpleNamespace(status="installed", kind="systemd-user"),
    )
    runner = CliRunner()

    host_result = runner.invoke(app, ["service", "host", "0.0.0.0"])
    assert host_result.exit_code == 0

    port_result = runner.invoke(
        app,
        ["service", "port", "--runtime-port", str(new_runtime_port)],
    )

    assert port_result.exit_code == 0
    assert "Requested runtime bind is busy" not in port_result.stdout
    assert "Requested runtime bind is busy" not in port_result.stderr
    config = read_runtime_config(get_settings())
    assert config["runtime_host"] == "0.0.0.0"
    assert config["runtime_port"] == new_runtime_port
    get_settings.cache_clear()

def test_service_start_command_exits_zero_when_daemon_was_started(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service start` should delegate to the managed-service start helper."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.service.start_managed_runtime_service",
        lambda settings: SimpleNamespace(status="installed", kind="systemd-user"),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "start"])

    assert result.exit_code == 0
    assert "status=installed" in result.stdout
    get_settings.cache_clear()


def test_service_run_managed_command_delegates_to_start_runtime(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Hidden managed-service entrypoint should reuse the normal runtime starter."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[str] = []
    monkeypatch.setattr("afkbot.cli.commands.service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.service.run_start_command",
        lambda **kwargs: calls.append(str(kwargs["settings"].root_dir)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "run-managed"])

    assert result.exit_code == 0
    assert calls == [str(tmp_path)]
    get_settings.cache_clear()


def test_service_stop_command_exits_zero_when_daemon_was_stopped(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service stop` should succeed when the service stop helper reports success."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.service.stop_managed_runtime_service",
        lambda settings: SimpleNamespace(status="stopped", kind="systemd-user"),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "stop"])

    assert result.exit_code == 0
    assert "status=stopped" in result.stdout
    get_settings.cache_clear()


def test_service_status_command_fails_when_daemon_is_down(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service status` should exit non-zero when AFKBOT health probes are down."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.service.inspect_managed_runtime_service",
        lambda settings: SimpleNamespace(
            installed=True,
            running=False,
            kind="systemd-user",
            path=tmp_path / "afkbot.service",
            manager_state="active=inactive, enabled=enabled",
            runtime_ok=False,
            api_ok=False,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 1
    assert "daemon=not_running" in result.stdout
    get_settings.cache_clear()


def test_service_status_command_fails_when_daemon_is_manual_but_service_is_not_installed(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk service status` should stay non-zero when no managed service exists."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.service.inspect_managed_runtime_service",
        lambda settings: SimpleNamespace(
            installed=False,
            running=True,
            kind=None,
            path=None,
            manager_state=None,
            runtime_ok=True,
            api_ok=True,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 1
    assert "service: not_installed" in result.stdout
    assert "daemon=running" in result.stdout
    get_settings.cache_clear()


def test_service_help_mentions_host_command(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Service help should advertise the dedicated bind-host command."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    runner = CliRunner()

    result, output = invoke_plain_help(runner, app, ["service"])

    assert result.exit_code == 0
    assert "host" in output
    assert "Persist a new runtime host" in output
    get_settings.cache_clear()
