"""Tests for unified runtime `afk start` command."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from afkbot.services.upgrade import UpgradeApplyReport, UpgradeStepReport
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings
from tests.cli._rendering import invoke_plain_help


async def _no_pending_upgrades(settings):  # type: ignore[no-untyped-def]
    del settings
    return UpgradeApplyReport(changed=False, steps=())


def test_start_rejects_identical_ports(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Start command should reject same runtime/api port."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["start", "--runtime-port", "8080", "--api-port", "8080"],
    )
    assert result.exit_code != 0
    assert "must be different ports" in result.stderr
    get_settings.cache_clear()


def test_start_invokes_full_stack(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Start command should call unified async stack launcher with resolved params."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    calls: list[tuple[str, int, int, bool, tuple[str, ...], bool, str]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del persist_runtime_bind
        calls.append(
            (
                host,
                runtime_port,
                api_port,
                start_channels,
                channel_ids,
                strict_channels,
                str(settings.root_dir),
            )
        )

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["start", "--host", "127.0.0.1", "--runtime-port", "18080", "--api-port", "18081"],
    )
    assert result.exit_code == 0
    assert calls == [("127.0.0.1", 18080, 18081, True, (), False, str(get_settings().root_dir))]
    assert "runtime daemon: http://127.0.0.1:18080" in result.stdout
    assert "chat api/ws: http://127.0.0.1:18081" in result.stdout
    get_settings.cache_clear()


def test_start_runtime_port_override_updates_default_api_port(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When only runtime port is overridden, API default should follow runtime+1."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    calls: list[tuple[str, int, int, bool, tuple[str, ...]]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del settings, strict_channels, persist_runtime_bind
        calls.append((host, runtime_port, api_port, start_channels, channel_ids))

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["start", "--runtime-port", "19000"],
    )
    assert result.exit_code == 0
    assert calls
    _, runtime_port, api_port, start_channels, channel_ids = calls[0]
    assert runtime_port == 19000
    assert api_port == 19001
    assert start_channels is True
    assert channel_ids == ()
    get_settings.cache_clear()


def test_start_uses_auto_selected_exotic_port_when_runtime_port_is_unconfigured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When no runtime port is configured, start should use the resolved exotic default."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    calls: list[tuple[str, int, int, bool, tuple[str, ...]]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del settings, strict_channels, persist_runtime_bind
        calls.append((host, runtime_port, api_port, start_channels, channel_ids))

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    monkeypatch.setattr("afkbot.cli.commands.start.read_runtime_config", lambda settings: {})
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.resolve_default_runtime_port",
        lambda *, settings, host, runtime_config: 46341,
    )
    runner = CliRunner()

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert calls == [("127.0.0.1", 46341, 46342, True, ())]
    assert "runtime daemon: http://127.0.0.1:46341" in result.stdout
    assert "chat api/ws: http://127.0.0.1:46342" in result.stdout
    get_settings.cache_clear()


def test_start_persists_first_successful_auto_selected_runtime_port(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Auto-selected runtime port should be written once so later starts reuse it."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    writes: list[dict[str, object]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del api_port, start_channels, channel_ids, strict_channels
        assert persist_runtime_bind is True
        from afkbot.cli.commands.start import _persist_runtime_bind_defaults

        _persist_runtime_bind_defaults(settings=settings, host=host, runtime_port=runtime_port)

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    monkeypatch.setattr("afkbot.cli.commands.start.read_runtime_config", lambda settings: {})
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.resolve_default_runtime_port",
        lambda *, settings, host, runtime_config: 46341,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.write_runtime_config",
        lambda settings, *, config: writes.append(dict(config)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert writes == [{"runtime_host": "127.0.0.1", "runtime_port": 46341}]
    get_settings.cache_clear()


def test_start_repairs_persisted_runtime_port_conflict_and_persists_new_port(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """Configured ports occupied by a foreign process should auto-shift and persist."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    writes: list[dict[str, object]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del api_port, start_channels, channel_ids, strict_channels
        assert persist_runtime_bind is True
        from afkbot.cli.commands.start import _persist_runtime_bind_defaults

        _persist_runtime_bind_defaults(settings=settings, host=host, runtime_port=runtime_port)

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    monkeypatch.setattr(
        "afkbot.cli.commands.start.read_runtime_config",
        lambda settings: {"runtime_host": "127.0.0.1", "runtime_port": 46339},
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: runtime_port == 46341,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.probe_runtime_stack",
        lambda *, host, runtime_port, api_port=None, timeout_sec=1.0: SimpleNamespace(
            running=False,
            conflict=True,
            runtime=SimpleNamespace(ok=False),
            api=SimpleNamespace(ok=False),
        ),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.find_available_runtime_port",
        lambda *, host, preferred_port, attempts=64: 46341,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.write_runtime_config",
        lambda settings, *, config: writes.append(dict(config)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert writes == [{"runtime_host": "127.0.0.1", "runtime_port": 46341}]
    assert "runtime_port=46339" in result.stderr
    assert "runtime_port=46341" in result.stderr
    get_settings.cache_clear()


def test_start_refuses_to_shift_port_when_afkbot_daemon_is_already_running(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """A second `afk start` must not rewrite the configured port while AFKBOT is already live."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr(
        "afkbot.cli.commands.start.read_runtime_config",
        lambda settings: {"runtime_host": "127.0.0.1", "runtime_port": 46339},
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: False,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.start.probe_runtime_stack",
        lambda *, host, runtime_port, api_port=None, timeout_sec=1.0: SimpleNamespace(
            running=True,
            conflict=False,
            runtime=SimpleNamespace(ok=True),
            api=SimpleNamespace(ok=True),
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["start"])

    assert result.exit_code != 0
    assert "already running" in result.stderr.lower()
    get_settings.cache_clear()


def test_start_can_disable_external_channels(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """CLI flag should disable external channel adapters."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[tuple[bool, tuple[str, ...]]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del host, runtime_port, api_port, settings, strict_channels, persist_runtime_bind
        calls.append((start_channels, channel_ids))

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    runner = CliRunner()
    result = runner.invoke(app, ["start", "--no-channels"])
    assert result.exit_code == 0
    assert calls == [(False, ())]
    get_settings.cache_clear()


def test_start_rejects_pending_upgrades_by_default(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Start should fail closed when persisted-state upgrades are still pending."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    async def _fake_inspect_pending_upgrades(settings):  # type: ignore[no-untyped-def]
        del settings
        return UpgradeApplyReport(
            changed=True,
            steps=(
                UpgradeStepReport(
                    name="setup_state",
                    changed=True,
                    details="setup marker needs canonical rewrite or legacy-marker cleanup",
                ),
            ),
        )

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _fake_inspect_pending_upgrades)
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )
    runner = CliRunner()
    result = runner.invoke(app, ["start"])
    assert result.exit_code != 0
    assert "Run `afk upgrade apply` first" in result.stderr
    get_settings.cache_clear()


def test_start_can_bypass_pending_upgrade_guard(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Explicit override should allow startup when the operator accepts pending upgrades."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[tuple[str, int, int, bool, tuple[str, ...]]] = []

    async def _fake_inspect_pending_upgrades(settings):  # type: ignore[no-untyped-def]
        del settings
        return UpgradeApplyReport(
            changed=True,
            steps=(UpgradeStepReport(name="setup_state", changed=True, details="pending"),),
        )

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del settings, strict_channels, persist_runtime_bind
        calls.append((host, runtime_port, api_port, start_channels, channel_ids))

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _fake_inspect_pending_upgrades)
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    runner = CliRunner()
    result = runner.invoke(app, ["start", "--allow-pending-upgrades"])
    assert result.exit_code == 0
    assert calls
    get_settings.cache_clear()


def test_start_can_select_specific_channels(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """CLI should pass selected channel endpoint ids to the runtime launcher."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[tuple[bool, tuple[str, ...]]] = []

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del host, runtime_port, api_port, settings, strict_channels, persist_runtime_bind
        calls.append((start_channels, channel_ids))

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr(
        "afkbot.cli.commands.start.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    runner = CliRunner()
    result = runner.invoke(app, ["start", "--channel", "support-bot", "--channel", "sales-bot"])
    assert result.exit_code == 0
    assert calls == [(True, ("support-bot", "sales-bot"))]
    get_settings.cache_clear()


def test_start_formats_bind_conflicts_as_usage_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Port conflicts should surface a short operator-facing CLI error instead of a traceback."""

    # Arrange
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    async def _fake_run_full_stack(
        *,
        host: str,
        runtime_port: int,
        api_port: int,
        start_channels: bool,
        channel_ids: tuple[str, ...],
        strict_channels: bool,
        persist_runtime_bind: bool,
        settings,
    ) -> None:
        del host, runtime_port, api_port, start_channels, channel_ids, strict_channels, settings, persist_runtime_bind
        raise OSError(48, "address already in use")

    monkeypatch.setattr("afkbot.cli.commands.start._inspect_pending_upgrades", _no_pending_upgrades)
    monkeypatch.setattr("afkbot.cli.commands.start._run_full_stack", _fake_run_full_stack)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["start", "--runtime-port", "18080", "--api-port", "18081"])

    # Assert
    assert result.exit_code != 0
    assert "Runtime failed to bind a local port." in result.stderr
    assert "runtime_port=18080" in result.stderr
    assert "Choose free ports or stop the conflicting listener." in result.stderr
    get_settings.cache_clear()


def test_start_help_mentions_full_stack(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Help text should describe start as the single full-stack launcher."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    runner = CliRunner()
    result, output = invoke_plain_help(runner, app, ["start"])
    assert result.exit_code == 0
    assert "Start the full AFKBOT stack" in output
    assert "webhook" in output
    assert "ingress" in output
    assert "Chat API/WS port" in output
    assert "--channel" in output
    assert "--allow-pending-" in output
    get_settings.cache_clear()


async def test_run_full_stack_starts_and_stops_taskflow_runtime(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Internal launcher should manage both automation and Task Flow runtimes."""

    lifecycle: list[str] = []
    real_event_cls = asyncio.Event

    class _AutoStopEvent:
        def __init__(self) -> None:
            self._event = real_event_cls()
            loop = asyncio.get_running_loop()
            loop.call_soon(self._event.set)

        def set(self) -> None:
            self._event.set()

        def clear(self) -> None:
            self._event.clear()

        def is_set(self) -> bool:
            return self._event.is_set()

        async def wait(self) -> bool:
            await self._event.wait()
            return True

    class _FakeAutomationDaemon:
        def __init__(self, *, host: str, port: int) -> None:
            lifecycle.append(f"automation:init:{host}:{port}")

        def begin_shutdown(self) -> None:
            lifecycle.append("automation:begin_shutdown")

        async def start(self) -> None:
            lifecycle.append("automation:start")

        async def stop(self) -> None:
            lifecycle.append("automation:stop")

    class _FakeTaskFlowDaemon:
        def __init__(self, *, settings) -> None:  # type: ignore[no-untyped-def]
            lifecycle.append(f"taskflow:init:{settings.root_dir}")

        def begin_shutdown(self) -> None:
            lifecycle.append("taskflow:begin_shutdown")

        async def start(self) -> None:
            lifecycle.append("taskflow:start")

        async def stop(self) -> None:
            lifecycle.append("taskflow:stop")

    class _FakeChannelManager:
        def __init__(self, settings) -> None:  # type: ignore[no-untyped-def]
            del settings

        async def stop(self) -> None:
            lifecycle.append("channels:stop")

    class _FakeServer:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            del config
            self.should_exit = False

        async def serve(self) -> None:
            lifecycle.append("api:serve")
            while not self.should_exit:
                await asyncio.sleep(0)

    monkeypatch.setattr("afkbot.cli.commands.start.asyncio.Event", _AutoStopEvent)
    monkeypatch.setattr("afkbot.cli.commands.start.RuntimeDaemon", _FakeAutomationDaemon)
    monkeypatch.setattr("afkbot.cli.commands.start.TaskFlowRuntimeDaemon", _FakeTaskFlowDaemon)
    monkeypatch.setattr("afkbot.cli.commands.start.ChannelRuntimeManager", _FakeChannelManager)
    monkeypatch.setattr("afkbot.cli.commands.start._ManagedUvicornServer", _FakeServer)
    monkeypatch.setattr("afkbot.cli.commands.start.create_app", lambda: object())

    from afkbot.cli.commands.start import _run_full_stack
    from afkbot.settings import Settings

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'start_runtime.db'}",
    )
    await _run_full_stack(
        host="127.0.0.1",
        runtime_port=18080,
        api_port=18081,
        start_channels=False,
        channel_ids=(),
        strict_channels=False,
        persist_runtime_bind=False,
        settings=settings,
    )

    assert "automation:start" in lifecycle
    assert "taskflow:start" in lifecycle
    assert "api:serve" in lifecycle
    assert lifecycle[-3:] == ["channels:stop", "taskflow:stop", "automation:stop"]


async def test_run_full_stack_fails_when_api_server_exits_unexpectedly(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A clean API task exit before shutdown should fail the overall runtime start."""

    class _FakeAutomationDaemon:
        def __init__(self, *, host: str, port: int) -> None:
            del host, port

        def begin_shutdown(self) -> None:
            return

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

    class _FakeTaskFlowDaemon:
        def __init__(self, *, settings) -> None:  # type: ignore[no-untyped-def]
            del settings

        def begin_shutdown(self) -> None:
            return

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

    class _FakeChannelManager:
        def __init__(self, settings) -> None:  # type: ignore[no-untyped-def]
            del settings

        async def stop(self) -> None:
            return

    class _FakeServer:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            del config
            self.should_exit = False

        async def serve(self) -> None:
            return

    monkeypatch.setattr("afkbot.cli.commands.start.RuntimeDaemon", _FakeAutomationDaemon)
    monkeypatch.setattr("afkbot.cli.commands.start.TaskFlowRuntimeDaemon", _FakeTaskFlowDaemon)
    monkeypatch.setattr("afkbot.cli.commands.start.ChannelRuntimeManager", _FakeChannelManager)
    monkeypatch.setattr("afkbot.cli.commands.start._ManagedUvicornServer", _FakeServer)
    monkeypatch.setattr("afkbot.cli.commands.start.create_app", lambda: object())

    from afkbot.cli.commands.start import _run_full_stack
    from afkbot.settings import Settings

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'start_runtime.db'}",
    )

    try:
        await _run_full_stack(
            host="127.0.0.1",
            runtime_port=18080,
            api_port=18081,
            start_channels=False,
            channel_ids=(),
            strict_channels=False,
            persist_runtime_bind=False,
            settings=settings,
        )
    except RuntimeError as exc:
        assert "exited before shutdown was requested" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected runtime start to fail on unexpected API exit")
