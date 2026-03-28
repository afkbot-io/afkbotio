"""Tests for browser CLI command group."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.browser_runtime import BrowserRuntimeInstallResult, BrowserRuntimeStatus
from afkbot.services.lightpanda_runtime import LightpandaManagedStatus, LightpandaRunResult
from afkbot.settings import get_settings


def test_browser_status_json_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """browser status should return deterministic JSON on failure."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings=None: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_missing_package",
            reason="Playwright is not installed: ModuleNotFoundError",
            remediation="Run `afk browser install`.",
        ),
    )

    # Act
    result = runner.invoke(app, ["browser", "status", "--json"])

    # Assert
    assert result.exit_code == 1
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is False
    assert payload["error_code"] == "browser_runtime_missing_package"


def test_browser_install_json_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """browser install should emit deterministic JSON success payload."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.install_browser_runtime",
        lambda force=False, settings=None: BrowserRuntimeInstallResult(
            ok=True,
            error_code=None,
            reason="Browser runtime installed and ready.",
            package_installed=True,
            browser_installed=True,
        ),
    )

    # Act
    result = runner.invoke(app, ["browser", "install", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["package_installed"] is True
    assert payload["browser_installed"] is True


def test_browser_install_cancelled_before_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Interactive browser install should stop before making changes when user declines."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setattr("afkbot.cli.commands.browser.browser_install_wizard_enabled", lambda: False)
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings=None: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_missing_package",
            reason="Playwright is not installed: ModuleNotFoundError",
            remediation="Run `afk browser install`.",
        ),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.prompt_confirm",
        lambda **kwargs: False,
    )

    def _unexpected_install(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args, kwargs
        raise AssertionError("install_browser_runtime must not be called when user cancels")

    monkeypatch.setattr("afkbot.cli.commands.browser.install_browser_runtime", _unexpected_install)

    # Act
    result = runner.invoke(app, ["browser", "install"])

    # Assert
    assert result.exit_code == 0
    assert "cancelled" in result.stdout.lower()


def test_browser_install_cancelled_wizard_does_not_persist_backend_choice(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Interactive browser install should not persist wizard answers when confirmation is declined."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr("afkbot.cli.commands.browser.browser_install_wizard_enabled", lambda: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.browser_support.prompt_browser_backend",
        lambda **kwargs: "lightpanda_cdp",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser_support.prompt_browser_cdp_url",
        lambda **kwargs: "http://127.0.0.1:9222",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings=None: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_cdp_unavailable",
            reason="Configured CDP browser is unavailable: ConnectionRefusedError",
            remediation="Start Lightpanda and retry.",
            backend="lightpanda_cdp",
        ),
    )
    monkeypatch.setattr("afkbot.cli.commands.browser.prompt_confirm", lambda **kwargs: False)

    def _unexpected_install(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args, kwargs
        raise AssertionError("install_browser_runtime must not be called when user cancels")

    monkeypatch.setattr("afkbot.cli.commands.browser.install_browser_runtime", _unexpected_install)

    # Act
    result = runner.invoke(app, ["browser", "install"])

    # Assert
    assert result.exit_code == 0
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.browser_backend == "playwright_chromium"
    assert settings.browser_cdp_url is None
    assert "cancelled" in result.stdout.lower()
    get_settings.cache_clear()


def test_browser_install_prompt_accepts_and_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Interactive browser install should continue when user confirms."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setattr("afkbot.cli.commands.browser.browser_install_wizard_enabled", lambda: False)
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings=None: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_missing_package",
            reason="Playwright is not installed: ModuleNotFoundError",
            remediation="Run `afk browser install`.",
        ),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.prompt_confirm",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.install_browser_runtime",
        lambda force=False, settings=None: BrowserRuntimeInstallResult(
            ok=True,
            error_code=None,
            reason="Browser runtime installed and ready.",
            package_installed=True,
            browser_installed=True,
        ),
    )

    # Act
    result = runner.invoke(app, ["browser", "install"])

    # Assert
    assert result.exit_code == 0
    assert "installed and ready" in result.stdout.lower()


def test_browser_install_wizard_persists_lightpanda_backend(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Interactive browser install should persist backend choice and CDP URL from the wizard."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr("afkbot.cli.commands.browser.browser_install_wizard_enabled", lambda: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.browser_support.prompt_browser_backend",
        lambda **kwargs: "lightpanda_cdp",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser_support.prompt_browser_cdp_url",
        lambda **kwargs: "http://127.0.0.1:9222",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings=None: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_cdp_unavailable",
            reason="Configured CDP browser is unavailable: ConnectionRefusedError",
            remediation="Start Lightpanda and retry.",
            backend="lightpanda_cdp",
        ),
    )
    monkeypatch.setattr("afkbot.cli.commands.browser.prompt_confirm", lambda **kwargs: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.install_browser_runtime",
        lambda force=False, settings=None: BrowserRuntimeInstallResult(
            ok=True,
            error_code=None,
            reason="Playwright client is ready for Lightpanda CDP.",
            package_installed=True,
            browser_installed=False,
            backend="lightpanda_cdp",
        ),
    )

    # Act
    result = runner.invoke(app, ["browser", "install"])

    # Assert
    assert result.exit_code == 0
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.browser_backend == "lightpanda_cdp"
    assert settings.browser_cdp_url == "http://127.0.0.1:9222"
    assert "lightpanda" in result.stdout.lower()
    get_settings.cache_clear()


def test_browser_backend_command_persists_runtime_config(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """browser backend should persist the selected runtime backend."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    # Act
    result = runner.invoke(app, ["browser", "backend", "lightpanda_cdp", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["browser_backend"] == "lightpanda_cdp"
    assert payload["changed"] is True
    get_settings.cache_clear()
    assert get_settings().browser_backend == "lightpanda_cdp"
    get_settings.cache_clear()


def test_browser_headless_off_persists_runtime_config(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """browser headless off should persist non-headless mode for future processes."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    # Act
    result = runner.invoke(app, ["browser", "headless", "off", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["browser_headless"] is False
    assert payload["changed"] is True

    get_settings.cache_clear()
    assert get_settings().browser_headless is False
    get_settings.cache_clear()


def test_browser_close_json_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """browser close should return deterministic JSON payload."""

    # Arrange
    class _FakeManager:
        async def close_session(  # type: ignore[no-untyped-def]
            self,
            *,
            root_dir,
            profile_id,
            session_id,
            clear_persisted_state=False,
        ) -> bool:
            _ = root_dir, profile_id, session_id, clear_persisted_state
            return True

    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_session_manager",
        lambda: _FakeManager(),
    )

    # Act
    result = runner.invoke(app, ["browser", "close", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["closed"] is True


def test_browser_close_json_with_clear_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """browser close should pass clear-state flag through to the manager and payload."""

    # Arrange
    seen: dict[str, object] = {}

    class _FakeManager:
        async def close_session(  # type: ignore[no-untyped-def]
            self,
            *,
            root_dir,
            profile_id,
            session_id,
            clear_persisted_state=False,
        ) -> bool:
            _ = root_dir, profile_id, session_id
            seen["clear_persisted_state"] = clear_persisted_state
            return True

    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_session_manager",
        lambda: _FakeManager(),
    )

    # Act
    result = runner.invoke(app, ["browser", "close", "--clear-state", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["clear_state"] is True
    assert seen["clear_persisted_state"] is True


def test_browser_cdp_url_command_normalizes_shorthand(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """browser cdp-url should persist shorthand host:port values as normalized URLs."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    # Act
    result = runner.invoke(app, ["browser", "cdp-url", "127.0.0.1:9222", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["browser_cdp_url"] == "http://127.0.0.1:9222"
    get_settings.cache_clear()
    assert get_settings().browser_cdp_url == "http://127.0.0.1:9222"
    get_settings.cache_clear()


def test_browser_status_json_includes_managed_lightpanda_details(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """browser status JSON should include managed Lightpanda details for that backend."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_BROWSER_BACKEND", "lightpanda_cdp")
    monkeypatch.setenv("AFKBOT_BROWSER_CDP_URL", "http://127.0.0.1:9222")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings=None: BrowserRuntimeStatus(
            ok=False,
            error_code="lightpanda_runtime_stopped",
            reason="Managed Lightpanda binary is installed but not running.",
            remediation="Run `afk browser start` to launch the managed Lightpanda CDP server.",
            backend="lightpanda_cdp",
        ),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.browser_support.get_lightpanda_managed_status",
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

    # Act
    result = runner.invoke(app, ["browser", "status", "--json"])

    # Assert
    assert result.exit_code == 1
    payload = json.loads(result.stdout.strip())
    assert payload["managed_lightpanda"]["binary_installed"] is True
    assert payload["managed_lightpanda"]["endpoint_is_local"] is True
    get_settings.cache_clear()


def test_browser_start_json_returns_managed_runtime_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """browser start should expose deterministic JSON when Lightpanda starts successfully."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_BROWSER_BACKEND", "lightpanda_cdp")
    monkeypatch.setenv("AFKBOT_BROWSER_CDP_URL", "http://127.0.0.1:9222")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.start_managed_browser_runtime",
        lambda settings: LightpandaRunResult(
            ok=True,
            error_code=None,
            reason="Managed Lightpanda runtime started on 127.0.0.1:9222.",
            changed=True,
            running=True,
            pid=4321,
            binary_path="/tmp/lightpanda",
            log_path="/tmp/lightpanda.log",
        ),
    )

    # Act
    result = runner.invoke(app, ["browser", "start", "--json"])

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["running"] is True
    assert payload["pid"] == 4321
    get_settings.cache_clear()
