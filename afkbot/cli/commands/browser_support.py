"""Support helpers for browser runtime CLI commands."""

from __future__ import annotations

import os
import platform
import sys

import typer

from afkbot.browser_backends import (
    DEFAULT_BROWSER_BACKEND,
    LIGHTPANDA_CDP,
    get_browser_backend_spec,
)
from afkbot.cli.presentation.browser_prompts import prompt_browser_backend, prompt_browser_cdp_url
from afkbot.cli.presentation.prompt_i18n import (
    PromptLanguage,
    resolve_prompt_language as resolve_cli_prompt_language,
)
from afkbot.cli.presentation.setup_prompts import msg
from afkbot.services.browser_cdp import normalize_browser_cdp_url
from afkbot.services.browser_runtime import (
    BrowserRuntimeInstallResult,
    BrowserRuntimeStatus,
)
from afkbot.services.lightpanda_runtime import (
    LightpandaRunResult,
    get_lightpanda_managed_status,
    start_lightpanda_runtime,
    stop_lightpanda_runtime,
)
from afkbot.services.setup.runtime_store import read_runtime_config, write_runtime_config
from afkbot.settings import Settings, get_settings


def status_payload(result: BrowserRuntimeStatus, *, settings: Settings) -> dict[str, object]:
    """Build deterministic JSON for browser runtime status responses."""

    payload: dict[str, object] = {
        "ok": result.ok,
        "error_code": result.error_code,
        "reason": result.reason,
        "remediation": result.remediation,
        "backend": result.backend or settings.browser_backend,
        "browser_cdp_url": active_browser_cdp_url(settings),
    }
    managed_lightpanda = lightpanda_status_payload(settings)
    if managed_lightpanda is not None:
        payload["managed_lightpanda"] = managed_lightpanda
    return payload


def install_payload(result: BrowserRuntimeInstallResult, *, settings: Settings) -> dict[str, object]:
    """Build deterministic JSON for browser runtime install responses."""

    payload: dict[str, object] = {
        "ok": result.ok,
        "error_code": result.error_code,
        "reason": result.reason,
        "package_installed": result.package_installed,
        "browser_installed": result.browser_installed,
        "backend": result.backend or settings.browser_backend,
        "browser_cdp_url": active_browser_cdp_url(settings),
    }
    managed_lightpanda = lightpanda_status_payload(settings)
    if managed_lightpanda is not None:
        payload["managed_lightpanda"] = managed_lightpanda
    return payload


def format_status_text(result: BrowserRuntimeStatus, *, settings: Settings) -> str:
    """Render human-readable browser runtime status text."""

    backend = format_backend_text(settings, backend=result.backend)
    if result.ok:
        text = f"browser: ok ({backend}) - {result.reason}"
    elif result.remediation:
        text = f"browser: fail ({backend}; {result.error_code}) - {result.reason}. {result.remediation}"
    else:
        text = f"browser: fail ({backend}; {result.error_code}) - {result.reason}"
    managed_status = format_lightpanda_status(settings)
    if managed_status is None:
        return text
    return f"{text}\n{managed_status}"


def format_install_text(result: BrowserRuntimeInstallResult, *, settings: Settings) -> str:
    """Render human-readable browser runtime install text."""

    if result.ok:
        return result.reason
    backend = format_backend_text(settings, backend=result.backend)
    return f"browser install failed ({backend}; {result.error_code}): {result.reason}"


def headless_label(enabled: bool) -> str:
    """Render one boolean headless flag using CLI labels."""

    return "on" if enabled else "off"


def headless_env_override_active() -> bool:
    """Return whether the browser headless env override is active."""

    raw_value = os.getenv("AFKBOT_BROWSER_HEADLESS")
    return raw_value is not None and bool(raw_value.strip())


def should_confirm_browser_install(*, force: bool, status: BrowserRuntimeStatus) -> bool:
    """Return whether interactive browser install should ask for confirmation."""

    return force or not status.ok


def browser_install_question(
    *,
    force: bool,
    status: BrowserRuntimeStatus,
    settings: Settings,
    lang: PromptLanguage,
) -> str:
    """Build browser install confirmation text."""

    backend_id = status.backend or settings.browser_backend
    backend_spec = get_browser_backend_spec(backend_id)
    actions = [
        msg(
            lang,
            en="Install Playwright Python package if it is missing.",
            ru="Установить Python-пакет Playwright, если он отсутствует.",
        ),
        msg(
            lang,
            en=f"Use browser backend: {backend_spec.label}.",
            ru=f"Использовать browser backend: {backend_spec.label}.",
        ),
    ]
    if backend_spec.requires_cdp_url:
        actions.append(
            msg(
                lang,
                en="Prepare Playwright client for connecting to an external CDP browser such as Lightpanda.",
                ru="Подготовить клиент Playwright для подключения к внешнему CDP-браузеру, например Lightpanda.",
            )
        )
        actions.append(
            msg(
                lang,
                en=f"Use CDP endpoint: {active_browser_cdp_url(settings) or '(not set)'}.",
                ru=f"Использовать CDP endpoint: {active_browser_cdp_url(settings) or '(not set)'}.",
            )
        )
    else:
        actions.append(
            msg(
                lang,
                en="Install Chromium runtime used by browser.control.",
                ru="Установить Chromium runtime, который использует browser.control.",
            )
        )
    if force:
        actions.append(
            msg(
                lang,
                en="Reinstall the browser runtime even if it is already available.",
                ru="Переустановить браузерный runtime, даже если он уже доступен.",
            )
        )
    if platform.system().lower() == "linux":
        actions.append(
            msg(
                lang,
                en="On Linux, Playwright may install required system libraries via the OS package manager.",
                ru="На Linux Playwright может установить необходимые системные библиотеки через пакетный менеджер ОС.",
            )
        )
    intro = msg(lang, en="This command will do the following:", ru="Эта команда выполнит следующее:")
    outro = msg(lang, en="Continue?", ru="Продолжить?")
    return f"{intro}\n- " + "\n- ".join(actions) + f"\n\n{outro}"


def resolve_prompt_language(settings: Settings) -> PromptLanguage:
    """Resolve interactive prompt language from persisted runtime config."""

    return resolve_cli_prompt_language(settings=settings, value=None, ru=False)


def browser_install_wizard_enabled() -> bool:
    """Return whether interactive browser-install prompts should run."""

    return sys.stdin.isatty() and sys.stdout.isatty()


def collect_browser_install_wizard_updates(
    *,
    settings: Settings,
    lang: PromptLanguage,
) -> dict[str, object | None]:
    """Collect backend-specific browser install settings before confirmation/install."""

    selected_backend = prompt_browser_backend(default=settings.browser_backend, lang=lang)
    backend_spec = get_browser_backend_spec(selected_backend)
    updates: dict[str, object | None] = {"browser_backend": selected_backend}
    if backend_spec.requires_cdp_url:
        updates["browser_cdp_url"] = prompt_browser_cdp_url(
            default=settings.browser_cdp_url or "",
            lang=lang,
        )
    return updates


def preview_runtime_config_updates(settings: Settings, **updates: object | None) -> Settings:
    """Return preview settings for browser-related runtime-config updates without persisting them."""

    normalized_updates = normalize_browser_runtime_updates(**updates)
    preview_updates = {key: value for key, value in normalized_updates.items() if value is not None}
    if not preview_updates:
        return settings
    return settings.model_copy(update=preview_updates)


def persist_runtime_config_updates(
    settings: Settings,
    **updates: object | None,
) -> tuple[Settings, bool]:
    """Persist browser-related runtime-config updates and return refreshed settings."""

    current_config = dict(read_runtime_config(settings))
    changed = False
    defaults: dict[str, object | None] = {
        "browser_backend": DEFAULT_BROWSER_BACKEND,
        "browser_cdp_url": None,
        "browser_headless": True,
    }
    normalized_updates = normalize_browser_runtime_updates(**updates)
    for key, normalized_value in normalized_updates.items():
        if key in defaults and normalized_value == defaults[key]:
            if key in current_config:
                current_config.pop(key, None)
                changed = True
            continue
        if normalized_value is None:
            if key in current_config:
                current_config.pop(key, None)
                changed = True
            continue
        if current_config.get(key) == normalized_value:
            continue
        current_config[key] = normalized_value
        changed = True
    if not changed:
        return settings, False
    write_runtime_config(settings, config=current_config)
    get_settings.cache_clear()
    return get_settings(), True


def normalize_browser_runtime_updates(**updates: object | None) -> dict[str, object | None]:
    """Normalize browser-related runtime-config values before preview or persistence."""

    normalized_updates: dict[str, object | None] = {}
    for key, value in updates.items():
        normalized_value = value
        if key == "browser_cdp_url" and isinstance(normalized_value, str):
            try:
                normalized_value = normalize_browser_cdp_url(normalized_value)
            except ValueError as exc:
                raise typer.BadParameter(f"browser CDP URL is invalid: {exc}") from exc
        normalized_updates[key] = normalized_value
    return normalized_updates


def active_browser_cdp_url(settings: Settings) -> str | None:
    """Return active CDP URL only for backends that require it."""

    backend_spec = get_browser_backend_spec(settings.browser_backend)
    if not backend_spec.requires_cdp_url:
        return None
    return settings.browser_cdp_url


def format_backend_text(settings: Settings, *, backend: str | None = None) -> str:
    """Format backend id with active endpoint details when relevant."""

    backend_id = backend or settings.browser_backend
    cdp_url = active_browser_cdp_url(settings)
    if backend_id == LIGHTPANDA_CDP and cdp_url:
        return f"{backend_id} @ {cdp_url}"
    return backend_id


def start_managed_browser_runtime(settings: Settings) -> LightpandaRunResult:
    """Start the active managed browser runtime when supported by the backend."""

    if settings.browser_backend != LIGHTPANDA_CDP:
        return LightpandaRunResult(
            ok=False,
            error_code="browser_backend_not_managed",
            reason=(
                f"Managed browser start is only supported for {LIGHTPANDA_CDP}. "
                f"Current backend is {settings.browser_backend}."
            ),
            changed=False,
            running=False,
            pid=None,
            binary_path="",
            log_path="",
        )
    return start_lightpanda_runtime(settings=settings)


def stop_managed_browser_runtime(settings: Settings) -> LightpandaRunResult:
    """Stop the active managed browser runtime when supported by the backend."""

    if settings.browser_backend != LIGHTPANDA_CDP:
        return LightpandaRunResult(
            ok=False,
            error_code="browser_backend_not_managed",
            reason=(
                f"Managed browser stop is only supported for {LIGHTPANDA_CDP}. "
                f"Current backend is {settings.browser_backend}."
            ),
            changed=False,
            running=False,
            pid=None,
            binary_path="",
            log_path="",
        )
    return stop_lightpanda_runtime(settings=settings)


def managed_runtime_payload(result: LightpandaRunResult, *, settings: Settings) -> dict[str, object]:
    """Return deterministic JSON payload for managed Lightpanda control commands."""

    return {
        "ok": result.ok,
        "error_code": result.error_code,
        "reason": result.reason,
        "changed": result.changed,
        "running": result.running,
        "pid": result.pid,
        "backend": settings.browser_backend,
        "browser_cdp_url": active_browser_cdp_url(settings),
        "binary_path": result.binary_path or None,
        "log_path": result.log_path or None,
    }


def lightpanda_status_payload(settings: Settings) -> dict[str, object] | None:
    """Return managed Lightpanda status details for browser status/install payloads."""

    if settings.browser_backend != LIGHTPANDA_CDP:
        return None
    status = get_lightpanda_managed_status(settings)
    return {
        "supported": status.supported,
        "endpoint_url": status.endpoint_url,
        "endpoint_is_local": status.endpoint_is_local,
        "binary_path": status.binary_path,
        "binary_installed": status.binary_installed,
        "running": status.running,
        "pid": status.pid,
        "log_path": status.log_path,
    }


def format_lightpanda_status(settings: Settings) -> str | None:
    """Render one extra managed-Lightpanda status line when that backend is active."""

    if settings.browser_backend != LIGHTPANDA_CDP:
        return None
    status = get_lightpanda_managed_status(settings)
    support = "supported" if status.supported else "unsupported"
    install_state = "installed" if status.binary_installed else "not installed"
    run_state = f"running pid={status.pid}" if status.running else "stopped"
    endpoint_mode = "local endpoint" if status.endpoint_is_local else "external endpoint"
    return (
        "managed lightpanda: "
        f"{support}, {endpoint_mode}, {install_state}, {run_state}, "
        f"binary={status.binary_path}, log={status.log_path}"
    )


__all__ = [
    "active_browser_cdp_url",
    "browser_install_question",
    "browser_install_wizard_enabled",
    "collect_browser_install_wizard_updates",
    "format_backend_text",
    "format_install_text",
    "format_lightpanda_status",
    "format_status_text",
    "headless_env_override_active",
    "headless_label",
    "install_payload",
    "lightpanda_status_payload",
    "managed_runtime_payload",
    "normalize_browser_runtime_updates",
    "persist_runtime_config_updates",
    "preview_runtime_config_updates",
    "resolve_prompt_language",
    "should_confirm_browser_install",
    "start_managed_browser_runtime",
    "status_payload",
    "stop_managed_browser_runtime",
]
