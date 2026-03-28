"""Browser runtime management for CLI and health checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import platform
import subprocess
import sys
import textwrap

from afkbot.browser_backends import LIGHTPANDA_CDP, LIGHTPANDA_DEFAULT_CDP_URL, PLAYWRIGHT_CHROMIUM
from afkbot.services.lightpanda_runtime import (
    get_lightpanda_managed_status,
    install_lightpanda_binary,
    lightpanda_runtime_hint,
)
from afkbot.settings import Settings, get_settings

_PLAYWRIGHT_PACKAGE = "playwright"
_PLAYWRIGHT_BROWSER_NAME = "chromium"
_STATUS_TIMEOUT_SEC = 20

_PLAYWRIGHT_STATUS_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import json

    REMEDIATION = "Run `afk browser install`."

    async def main() -> int:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_missing_package",
                        "reason": f"Playwright is not installed: {exc.__class__.__name__}",
                        "remediation": REMEDIATION,
                    },
                    ensure_ascii=True,
                )
            )
            return 1

        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "error_code": None,
                        "reason": "ready",
                        "backend": "playwright_chromium",
                    },
                    ensure_ascii=True,
                )
            )
            return 0
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_unavailable",
                        "reason": (
                            "Playwright browser runtime is unavailable: "
                            f"{exc.__class__.__name__}: {exc}"
                        ),
                        "remediation": REMEDIATION,
                    },
                    ensure_ascii=True,
                )
            )
            return 1
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass

    raise SystemExit(asyncio.run(main()))
    """
).strip()

_LIGHTPANDA_CDP_STATUS_SCRIPT = (
    textwrap.dedent(
        """
    import asyncio
    import json
    import os

    REMEDIATION = (
        "Set `afk browser cdp-url __AFKBOT_LIGHTPANDA_DEFAULT_CDP_URL__`, start Lightpanda, "
        "or switch backend with `afk browser backend playwright_chromium`."
    )

    async def main() -> int:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_runtime_missing_package",
                        "reason": f"Playwright is not installed: {exc.__class__.__name__}",
                        "remediation": "Run `afk browser install`.",
                        "backend": "lightpanda_cdp",
                    },
                    ensure_ascii=True,
                )
            )
            return 1
        cdp_url = (os.getenv("AFKBOT_BROWSER_CDP_URL") or "").strip()
        if not cdp_url:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_cdp_url_missing",
                        "reason": "CDP URL is not configured for lightpanda_cdp backend.",
                        "remediation": REMEDIATION,
                        "backend": "lightpanda_cdp",
                    },
                    ensure_ascii=True,
                )
            )
            return 1

        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "error_code": None,
                        "reason": "ready",
                        "backend": "lightpanda_cdp",
                    },
                    ensure_ascii=True,
                )
            )
            return 0
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "browser_cdp_unavailable",
                        "reason": (
                            "Configured CDP browser is unavailable: "
                            f"{exc.__class__.__name__}: {exc}"
                        ),
                        "remediation": REMEDIATION,
                        "backend": "lightpanda_cdp",
                    },
                    ensure_ascii=True,
                )
            )
            return 1
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass

    raise SystemExit(asyncio.run(main()))
    """
    )
    .replace("__AFKBOT_LIGHTPANDA_DEFAULT_CDP_URL__", LIGHTPANDA_DEFAULT_CDP_URL)
    .strip()
)


@dataclass(frozen=True, slots=True)
class BrowserRuntimeStatus:
    """Current browser runtime readiness in this Python environment."""

    ok: bool
    error_code: str | None
    reason: str
    remediation: str | None = None
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserRuntimeInstallResult:
    """Outcome of browser runtime installation/bootstrap."""

    ok: bool
    error_code: str | None
    reason: str
    package_installed: bool
    browser_installed: bool
    backend: str | None = None


def get_browser_runtime_status(settings: Settings | None = None) -> BrowserRuntimeStatus:
    """Return browser runtime readiness for the current Python interpreter."""

    effective_settings = settings or get_settings()
    backend = effective_settings.browser_backend
    env = None
    lightpanda_status = None
    if backend == LIGHTPANDA_CDP:
        lightpanda_status = get_lightpanda_managed_status(effective_settings)
        env = dict(os.environ)
        env["AFKBOT_BROWSER_CDP_URL"] = effective_settings.browser_cdp_url or ""
        script = _LIGHTPANDA_CDP_STATUS_SCRIPT
    else:
        script = _PLAYWRIGHT_STATUS_SCRIPT

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_SEC,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_check_timeout",
            reason=f"browser status probe exceeded {_STATUS_TIMEOUT_SEC} seconds",
            remediation=_backend_install_remediation(effective_settings),
            backend=backend,
        )
    payload = _parse_json_payload(result.stdout)
    if payload is None:
        reason = _collapse_stream_text(result.stderr) or _collapse_stream_text(result.stdout)
        return BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_check_failed",
            reason=reason or f"browser status probe exited with code {result.returncode}",
            remediation=_backend_install_remediation(effective_settings),
            backend=backend,
        )
    runtime_status = BrowserRuntimeStatus(
        ok=bool(payload.get("ok") is True),
        error_code=_normalize_optional_str(payload.get("error_code")),
        reason=_normalize_optional_str(payload.get("reason")) or "unknown browser runtime status",
        remediation=_normalize_optional_str(payload.get("remediation")),
        backend=_normalize_optional_str(payload.get("backend")) or backend,
    )
    if (
        backend == LIGHTPANDA_CDP
        and lightpanda_status is not None
        and (effective_settings.browser_cdp_url or "").strip()
        and lightpanda_status.endpoint_is_local
        and lightpanda_status.binary_installed
        and not lightpanda_status.running
        and runtime_status.error_code == "browser_cdp_unavailable"
    ):
        return BrowserRuntimeStatus(
            ok=False,
            error_code="lightpanda_runtime_stopped",
            reason="Managed Lightpanda binary is installed but not running.",
            remediation=lightpanda_runtime_hint(effective_settings),
            backend=backend,
        )
    return runtime_status


def install_browser_runtime(
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> BrowserRuntimeInstallResult:
    """Install browser runtime dependencies for the active backend."""

    effective_settings = settings or get_settings()
    backend = effective_settings.browser_backend
    initial_status = get_browser_runtime_status(effective_settings)
    package_installed = False
    browser_installed = False

    should_install_package = force or initial_status.error_code == "browser_runtime_missing_package"
    if should_install_package:
        package_result = _run_command(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", _PLAYWRIGHT_PACKAGE]
        )
        if package_result.returncode != 0:
            return BrowserRuntimeInstallResult(
                ok=False,
                error_code="browser_package_install_failed",
                reason=_command_failure_reason(package_result, fallback="Failed to install Playwright package"),
                package_installed=False,
                browser_installed=False,
                backend=backend,
            )
        package_installed = True

    if backend == PLAYWRIGHT_CHROMIUM and (force or not initial_status.ok):
        browser_result = None
        browser_installed = False
        for command in _browser_install_commands():
            browser_result = _run_command(command)
            if browser_result.returncode == 0:
                browser_installed = True
                break
        if not browser_installed:
            assert browser_result is not None
            return BrowserRuntimeInstallResult(
                ok=False,
                error_code="browser_binary_install_failed",
                reason=_command_failure_reason(
                    browser_result,
                    fallback=f"Failed to install Playwright {_PLAYWRIGHT_BROWSER_NAME} runtime",
                ),
                package_installed=package_installed,
                browser_installed=False,
                backend=backend,
            )

    if backend == LIGHTPANDA_CDP:
        configured_cdp_url = (effective_settings.browser_cdp_url or "").strip()
        lightpanda_status = get_lightpanda_managed_status(effective_settings)
        if configured_cdp_url and lightpanda_status.endpoint_is_local:
            lightpanda_install_result = install_lightpanda_binary(
                settings=effective_settings,
                force=force,
            )
            if lightpanda_install_result.ok:
                browser_installed = lightpanda_install_result.changed
            elif lightpanda_install_result.error_code != "lightpanda_binary_unsupported_platform":
                return BrowserRuntimeInstallResult(
                    ok=False,
                    error_code=lightpanda_install_result.error_code,
                    reason=lightpanda_install_result.reason,
                    package_installed=package_installed,
                    browser_installed=False,
                    backend=backend,
                )

    final_status = get_browser_runtime_status(effective_settings)
    if backend == LIGHTPANDA_CDP:
        if not (effective_settings.browser_cdp_url or "").strip():
            return BrowserRuntimeInstallResult(
                ok=False,
                error_code="browser_cdp_url_missing",
                reason=(
                    "CDP URL is not configured for Lightpanda backend. "
                    f"Set `afk browser cdp-url {LIGHTPANDA_DEFAULT_CDP_URL}` or rerun "
                    "`afk browser install` interactively."
                ),
                package_installed=package_installed,
                browser_installed=browser_installed,
                backend=backend,
            )
        if final_status.ok:
            return BrowserRuntimeInstallResult(
                ok=True,
                error_code=None,
                reason="Lightpanda CDP runtime is ready.",
                package_installed=package_installed,
                browser_installed=browser_installed,
                backend=backend,
            )
        if final_status.error_code in {
            "browser_cdp_unavailable",
            "browser_runtime_check_timeout",
            "lightpanda_runtime_stopped",
        }:
            return BrowserRuntimeInstallResult(
                ok=True,
                error_code=None,
                reason=(
                    "Playwright client is ready for Lightpanda CDP. "
                    f"{lightpanda_runtime_hint(effective_settings)}"
                ),
                package_installed=package_installed,
                browser_installed=browser_installed,
                backend=backend,
            )
    if not final_status.ok:
        reason = final_status.reason
        if backend == PLAYWRIGHT_CHROMIUM and _is_linux():
            reason = (
                f"{reason} On Linux install dependencies with: "
                f"python -m playwright install --with-deps {_PLAYWRIGHT_BROWSER_NAME}"
            )
        return BrowserRuntimeInstallResult(
            ok=False,
            error_code=final_status.error_code or "browser_runtime_unavailable",
            reason=reason,
            package_installed=package_installed,
            browser_installed=browser_installed,
            backend=backend,
        )

    if package_installed or browser_installed:
        reason = "Browser runtime installed and ready."
    else:
        reason = "Browser runtime is already ready."
    return BrowserRuntimeInstallResult(
        ok=True,
        error_code=None,
        reason=reason,
        package_installed=package_installed,
        browser_installed=browser_installed,
        backend=backend,
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def _is_linux() -> bool:
    """Return whether current runtime platform is Linux."""

    return platform.system().lower() == "linux"


def _browser_install_commands() -> list[list[str]]:
    """Return ordered playwright install commands for browser binaries."""

    base = [sys.executable, "-m", "playwright", "install", _PLAYWRIGHT_BROWSER_NAME]
    if not _is_linux():
        return [base]
    with_deps = [sys.executable, "-m", "playwright", "install", "--with-deps", _PLAYWRIGHT_BROWSER_NAME]
    return [with_deps, base]


def _backend_install_remediation(settings: Settings) -> str:
    if settings.browser_backend == LIGHTPANDA_CDP:
        return lightpanda_runtime_hint(settings)
    return "Run `afk browser install`."


def _command_failure_reason(
    result: subprocess.CompletedProcess[str],
    *,
    fallback: str,
) -> str:
    stderr = _collapse_stream_text(result.stderr)
    if stderr:
        return stderr
    stdout = _collapse_stream_text(result.stdout)
    if stdout:
        return stdout
    return fallback


def _parse_json_payload(raw: str) -> dict[str, object] | None:
    trimmed = raw.strip()
    if not trimmed:
        return None
    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


def _collapse_stream_text(raw: str) -> str:
    return " ".join(raw.strip().split())
