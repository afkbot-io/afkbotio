"""Tests for browser runtime status/install helpers."""

from __future__ import annotations

import json
import subprocess

from afkbot.services.browser_runtime import (
    BrowserRuntimeStatus,
    get_browser_runtime_status,
    install_browser_runtime,
)
from afkbot.services.lightpanda_runtime import LightpandaInstallResult, LightpandaManagedStatus
from afkbot.settings import Settings


def test_get_browser_runtime_status_parses_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Status helper should parse successful subprocess probe output."""

    # Arrange
    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args, kwargs
        return subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=0,
            stdout=json.dumps({"ok": True, "error_code": None, "reason": "ready"}),
            stderr="",
        )

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)

    # Act
    result = get_browser_runtime_status()

    # Assert
    assert result.ok is True
    assert result.error_code is None
    assert result.reason == "ready"


def test_get_browser_runtime_status_handles_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Status helper should fail deterministically when probe times out."""

    # Arrange
    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args, kwargs
        raise subprocess.TimeoutExpired(cmd=["python", "-c", "..."], timeout=20)

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)

    # Act
    result = get_browser_runtime_status()

    # Assert
    assert result.ok is False
    assert result.error_code == "browser_runtime_check_timeout"
    assert "20 seconds" in result.reason


def test_get_browser_runtime_status_passes_cdp_url_env_for_lightpanda(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Lightpanda status probes should pass the configured CDP URL into the subprocess env."""

    # Arrange
    seen_env: dict[str, str] = {}

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        seen_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "error_code": None,
                    "reason": "ready",
                    "backend": "lightpanda_cdp",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.get_lightpanda_managed_status",
        lambda settings: LightpandaManagedStatus(
            supported=True,
            endpoint_url="http://127.0.0.1:9222",
            endpoint_is_local=True,
            binary_path="/tmp/lightpanda",
            binary_installed=False,
            pid=None,
            running=False,
            log_path="/tmp/lightpanda.log",
        ),
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url="http://127.0.0.1:9222")

    # Act
    result = get_browser_runtime_status(settings)

    # Assert
    assert result.ok is True
    assert result.backend == "lightpanda_cdp"
    assert seen_env["AFKBOT_BROWSER_CDP_URL"] == "http://127.0.0.1:9222"


def test_get_browser_runtime_status_keeps_missing_package_signal_for_lightpanda(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Lightpanda status must not mask missing Playwright package as managed-runtime stopped."""

    # Arrange
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=1,
            stdout=json.dumps(
                {
                    "ok": False,
                    "error_code": "browser_runtime_missing_package",
                    "reason": "Playwright is not installed: ModuleNotFoundError",
                    "remediation": "Run `afk browser install`.",
                    "backend": "lightpanda_cdp",
                }
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.get_lightpanda_managed_status",
        lambda settings: LightpandaManagedStatus(
            supported=True,
            endpoint_url="http://127.0.0.1:9222",
            endpoint_is_local=True,
            binary_path="/tmp/lightpanda",
            binary_installed=True,
            pid=None,
            running=False,
            log_path="/tmp/lightpanda.log",
        ),
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url="http://127.0.0.1:9222")

    # Act
    result = get_browser_runtime_status(settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "browser_runtime_missing_package"
    assert "Playwright is not installed" in result.reason


def test_install_browser_runtime_installs_missing_package_and_browser(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Install helper should run pip and browser install when package is missing."""

    # Arrange
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "afkbot.services.browser_runtime._browser_install_commands",
        lambda: [["python", "-m", "playwright", "install", "chromium"]],
    )
    outputs = iter(
        [
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_missing_package",
                        "reason": "Playwright is not installed: ModuleNotFoundError",
                        "remediation": "Run `afk browser install`.",
                    }
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-m", "pip", "install", "playwright"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-m", "playwright", "install", "chromium"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=0,
                stdout=json.dumps({"ok": True, "error_code": None, "reason": "ready"}),
                stderr="",
            ),
        ]
    )

    def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        calls.append(list(command))
        return next(outputs)

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)

    # Act
    result = install_browser_runtime()

    # Assert
    assert result.ok is True
    assert result.package_installed is True
    assert result.browser_installed is True
    assert calls[1][-1] == "playwright"
    assert calls[2][-2:] == ["install", "chromium"]


def test_install_browser_runtime_retries_browser_install_commands(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Install helper should retry browser install with fallback command list."""

    # Arrange
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "afkbot.services.browser_runtime._browser_install_commands",
        lambda: [
            ["python", "-m", "playwright", "install", "--with-deps", "chromium"],
            ["python", "-m", "playwright", "install", "chromium"],
        ],
    )
    outputs = iter(
        [
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_unavailable",
                        "reason": "runtime missing deps",
                        "remediation": "Run `afk browser install`.",
                    }
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-m", "playwright", "install", "--with-deps", "chromium"],
                returncode=1,
                stdout="",
                stderr="with-deps failed",
            ),
            subprocess.CompletedProcess(
                args=["python", "-m", "playwright", "install", "chromium"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=0,
                stdout=json.dumps({"ok": True, "error_code": None, "reason": "ready"}),
                stderr="",
            ),
        ]
    )

    def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        calls.append(list(command))
        return next(outputs)

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)

    # Act
    result = install_browser_runtime()

    # Assert
    assert result.ok is True
    assert result.browser_installed is True
    assert calls[1][-3:] == ["install", "--with-deps", "chromium"]
    assert calls[2][-2:] == ["install", "chromium"]


def test_install_browser_runtime_prepares_lightpanda_even_when_endpoint_is_down(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Lightpanda install should succeed after client prep even when CDP endpoint is not yet reachable."""

    # Arrange
    calls: list[list[str]] = []
    outputs = iter(
        [
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_missing_package",
                        "reason": "Playwright is not installed: ModuleNotFoundError",
                        "remediation": "Run `afk browser install`.",
                        "backend": "lightpanda_cdp",
                    }
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-m", "pip", "install", "playwright"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_cdp_unavailable",
                        "reason": "Configured CDP browser is unavailable: ConnectionRefusedError",
                        "remediation": "Start Lightpanda and retry.",
                        "backend": "lightpanda_cdp",
                    }
                ),
                stderr="",
            ),
        ]
    )

    def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        calls.append(list(command))
        return next(outputs)

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.get_lightpanda_managed_status",
        lambda settings: LightpandaManagedStatus(
            supported=True,
            endpoint_url="http://127.0.0.1:9222",
            endpoint_is_local=True,
            binary_path="/tmp/lightpanda",
            binary_installed=False,
            pid=None,
            running=False,
            log_path="/tmp/lightpanda.log",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.install_lightpanda_binary",
        lambda settings, force=False: LightpandaInstallResult(
            ok=True,
            error_code=None,
            reason="Managed Lightpanda binary installed.",
            changed=True,
            binary_path="/tmp/lightpanda",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.lightpanda_runtime_hint",
        lambda settings: "Run `afk browser start` to launch the managed Lightpanda CDP server.",
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url="http://127.0.0.1:9222")

    # Act
    result = install_browser_runtime(settings=settings)

    # Assert
    assert result.ok is True
    assert result.package_installed is True
    assert result.browser_installed is True
    assert "afk browser start" in result.reason
    assert calls[1][-1] == "playwright"


def test_install_browser_runtime_requires_cdp_url_for_lightpanda(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Lightpanda install should fail clearly when no CDP URL is configured."""

    # Arrange
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=1,
            stdout=json.dumps(
                {
                    "ok": False,
                    "error_code": "browser_cdp_url_missing",
                    "reason": "CDP URL is not configured for lightpanda_cdp backend.",
                    "remediation": "Configure one.",
                    "backend": "lightpanda_cdp",
                }
            ),
            stderr="",
        ),
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url=None)

    # Act
    result = install_browser_runtime(settings=settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "browser_cdp_url_missing"
    assert "CDP URL is not configured" in result.reason


def test_install_browser_runtime_skips_lightpanda_binary_install_until_cdp_is_configured(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Lightpanda install should not download the managed binary before CDP URL is configured."""

    # Arrange
    outputs = iter(
        [
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_missing_package",
                        "reason": "Playwright is not installed: ModuleNotFoundError",
                        "remediation": "Run `afk browser install`.",
                        "backend": "lightpanda_cdp",
                    }
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-m", "pip", "install", "playwright"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["python", "-c", "..."],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_cdp_url_missing",
                        "reason": "CDP URL is not configured for lightpanda_cdp backend.",
                        "remediation": "Configure one.",
                        "backend": "lightpanda_cdp",
                    }
                ),
                stderr="",
            ),
        ]
    )

    def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        _ = command, kwargs
        return next(outputs)

    monkeypatch.setattr("afkbot.services.browser_runtime.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.install_lightpanda_binary",
        lambda settings, force=False: (_ for _ in ()).throw(
            AssertionError("install_lightpanda_binary must not run before CDP URL is configured")
        ),
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url=None)

    # Act
    result = install_browser_runtime(settings=settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "browser_cdp_url_missing"
    assert result.package_installed is True
    assert result.browser_installed is False


def test_get_browser_runtime_status_reports_stopped_managed_lightpanda(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Lightpanda status should report managed binaries that are installed but not running."""

    # Arrange
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=1,
            stdout=json.dumps(
                {
                    "ok": False,
                    "error_code": "browser_cdp_unavailable",
                    "reason": "Configured CDP browser is unavailable: ConnectionRefusedError",
                    "remediation": "Start Lightpanda and retry.",
                    "backend": "lightpanda_cdp",
                }
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.get_lightpanda_managed_status",
        lambda settings: LightpandaManagedStatus(
            supported=True,
            endpoint_url="http://127.0.0.1:9222",
            endpoint_is_local=True,
            binary_path="/tmp/lightpanda",
            binary_installed=True,
            pid=None,
            running=False,
            log_path="/tmp/lightpanda.log",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.lightpanda_runtime_hint",
        lambda settings: "Run `afk browser start` to launch the managed Lightpanda CDP server.",
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url="http://127.0.0.1:9222")

    # Act
    result = get_browser_runtime_status(settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "lightpanda_runtime_stopped"
    assert "not running" in result.reason
    assert "afk browser start" in (result.remediation or "")


def test_install_browser_runtime_installs_managed_lightpanda_for_local_endpoint(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Local Lightpanda endpoints should install the managed binary and guide operators to start it."""

    # Arrange
    statuses = iter(
        [
            BrowserRuntimeStatus(
                ok=False,
                error_code="browser_runtime_missing_package",
                reason="Playwright is not installed",
                remediation="Run `afk browser install`.",
                backend="lightpanda_cdp",
            ),
            BrowserRuntimeStatus(
                ok=False,
                error_code="lightpanda_runtime_stopped",
                reason="Managed Lightpanda binary is installed but not running.",
                remediation="Run `afk browser start` to launch the managed Lightpanda CDP server.",
                backend="lightpanda_cdp",
            ),
        ]
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.get_browser_runtime_status",
        lambda settings=None: next(statuses),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime._run_command",
        lambda command: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.get_lightpanda_managed_status",
        lambda settings: LightpandaManagedStatus(
            supported=True,
            endpoint_url="http://127.0.0.1:9222",
            endpoint_is_local=True,
            binary_path="/tmp/lightpanda",
            binary_installed=False,
            pid=None,
            running=False,
            log_path="/tmp/lightpanda.log",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.install_lightpanda_binary",
        lambda settings, force=False: LightpandaInstallResult(
            ok=True,
            error_code=None,
            reason="Managed Lightpanda binary installed.",
            changed=True,
            binary_path="/tmp/lightpanda",
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.browser_runtime.lightpanda_runtime_hint",
        lambda settings: "Run `afk browser start` to launch the managed Lightpanda CDP server.",
    )
    settings = Settings(browser_backend="lightpanda_cdp", browser_cdp_url="http://127.0.0.1:9222")

    # Act
    result = install_browser_runtime(settings=settings)

    # Assert
    assert result.ok is True
    assert result.package_installed is True
    assert result.browser_installed is True
    assert "afk browser start" in result.reason
