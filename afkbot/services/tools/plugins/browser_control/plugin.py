"""Tool plugin for browser.control using Playwright."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field

from afkbot.browser_backends import LIGHTPANDA_CDP, LIGHTPANDA_DEFAULT_CDP_URL
from afkbot.services.lightpanda_runtime import lightpanda_runtime_hint
from afkbot.services.browser_snapshot import capture_browser_page_snapshot
from afkbot.services.browser_sessions import get_browser_session_manager
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters, build_tool_parameters
from afkbot.settings import Settings

PlaywrightError: type[BaseException]
PlaywrightTimeoutError: type[BaseException]
_PLAYWRIGHT_FACTORY: Callable[[], Any] | None
_PLAYWRIGHT_IMPORT_ERROR: Exception | None
_BROWSER_TIMEOUT_MS_MAX = 120_000

try:  # pragma: no cover - tested through monkeypatch
    from playwright.async_api import Error as _ImportedPlaywrightError
    from playwright.async_api import TimeoutError as _ImportedPlaywrightTimeoutError
    from playwright.async_api import async_playwright as _imported_playwright_factory
except Exception as exc:  # pragma: no cover - environment-dependent
    PlaywrightError = RuntimeError
    PlaywrightTimeoutError = TimeoutError
    _PLAYWRIGHT_FACTORY = None
    _PLAYWRIGHT_IMPORT_ERROR = exc
else:  # pragma: no cover - environment-dependent
    PlaywrightError = _ImportedPlaywrightError
    PlaywrightTimeoutError = _ImportedPlaywrightTimeoutError
    _PLAYWRIGHT_FACTORY = _imported_playwright_factory
    _PLAYWRIGHT_IMPORT_ERROR = None


class BrowserControlParams(RoutedToolParameters):
    """Parameters for browser.control."""

    action: Literal[
        "open",
        "navigate",
        "click",
        "fill",
        "press",
        "select",
        "check",
        "scroll",
        "wait",
        "content",
        "snapshot",
        "screenshot",
        "close",
    ]
    url: str | None = Field(default=None, min_length=1, max_length=4096)
    selector: str | None = Field(default=None, min_length=1, max_length=2048)
    text: str | None = Field(default=None, max_length=50_000)
    target_text: str | None = Field(default=None, min_length=1, max_length=2048)
    label: str | None = Field(default=None, min_length=1, max_length=2048)
    placeholder: str | None = Field(default=None, min_length=1, max_length=2048)
    field_name: str | None = Field(default=None, min_length=1, max_length=512)
    role: Literal[
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "option",
        "menuitem",
        "tab",
    ] | None = None
    key: str | None = Field(default=None, min_length=1, max_length=128)
    value: str | None = Field(default=None, max_length=50_000)
    path: str | None = Field(default=None, min_length=1, max_length=2048)
    max_chars: int = Field(default=20_000, ge=1, le=200_000)
    full_page: bool = True
    clear_state: bool = False
    exact: bool = False
    state: Literal["visible", "attached", "hidden", "detached"] = "visible"
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    timeout_ms: int = Field(default=15_000, ge=1, le=_BROWSER_TIMEOUT_MS_MAX)


@dataclass(slots=True)
class _BrowserSession:
    playwright: Any
    browser: Any
    page: Any


class _BrowserUnavailableError(RuntimeError):
    pass


class _BrowserSessionNotOpenError(RuntimeError):
    pass


def _browser_install_hint(settings: Settings | None = None) -> str:
    effective_settings = settings
    if effective_settings is not None and effective_settings.browser_backend == LIGHTPANDA_CDP:
        return f"Run `afk browser install`. {lightpanda_runtime_hint(effective_settings)}"
    return "Run `afk browser install`."


def _browser_error_metadata(*, error_code: str, reason: str) -> dict[str, object]:
    lowered = reason.strip().lower()
    if error_code == "browser_session_not_open":
        return {
            "browser_error_class": "browser_session_missing",
            "retryable": True,
            "requires_session_reset": False,
            "suggested_next_action": "open_session",
            "session_state": "missing",
        }
    if error_code == "browser_unavailable":
        return {
            "browser_error_class": "browser_runtime_missing",
            "retryable": False,
            "requires_session_reset": False,
            "suggested_next_action": "prepare_browser_runtime",
            "session_state": "unavailable",
        }
    if error_code == "browser_invalid":
        return {
            "browser_error_class": "browser_invalid_request",
            "retryable": False,
            "requires_session_reset": False,
            "suggested_next_action": "fix_request",
            "session_state": "unknown",
        }
    if "targetclosederror" in lowered or "target page, context or browser has been closed" in lowered:
        return {
            "browser_error_class": "browser_target_closed",
            "retryable": True,
            "requires_session_reset": True,
            "suggested_next_action": "reopen_session",
            "session_state": "dead",
        }
    if "timed out" in lowered:
        return {
            "browser_error_class": "browser_action_timeout",
            "retryable": True,
            "requires_session_reset": False,
            "suggested_next_action": "retry_or_wait",
            "session_state": "unknown",
        }
    return {
        "browser_error_class": "browser_action_failed",
        "retryable": False,
        "requires_session_reset": False,
        "suggested_next_action": "inspect_error",
        "session_state": "unknown",
    }


class BrowserControlTool(ToolBase):
    """Control one Playwright browser page for the current runtime session."""

    name = "browser.control"
    description = (
        "Control browser via Playwright actions: open, navigate, click, fill, press, "
        "select, check, scroll, wait, content, snapshot, screenshot, close. Supports "
        "selectors plus semantic targets like role/text/label/placeholder."
    )
    parameters_model = BrowserControlParams
    required_skill = "browser-control"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions = get_browser_session_manager()

    def parse_params(
        self,
        raw_params: dict[str, object] | Any,
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> BrowserControlParams:
        """Align tool timeout with browser action timeout unless caller overrides it explicitly."""

        payload = dict(raw_params)
        normalized_timeout_ms: int | None
        raw_timeout_ms = payload.get("timeout_ms")
        try:
            normalized_timeout_ms = int(raw_timeout_ms) if raw_timeout_ms is not None else None
        except (TypeError, ValueError):
            normalized_timeout_ms = None
        if payload.get("timeout_sec") is None:
            derived_timeout_sec = default_timeout_sec
            if normalized_timeout_ms is not None:
                derived_timeout_sec = max(1, (normalized_timeout_ms + 999) // 1000)
            payload["timeout_sec"] = derived_timeout_sec
        raw_timeout_sec = payload.get("timeout_sec")
        try:
            normalized_timeout_sec = int(raw_timeout_sec) if raw_timeout_sec is not None else None
        except (TypeError, ValueError):
            normalized_timeout_sec = None
        if normalized_timeout_ms is not None:
            max_timeout_ms = _BROWSER_TIMEOUT_MS_MAX
            if normalized_timeout_sec is not None:
                max_timeout_ms = min(max_timeout_ms, max(1, normalized_timeout_sec) * 1000)
            payload["timeout_ms"] = max(1, min(normalized_timeout_ms, max_timeout_ms))
        params = build_tool_parameters(
            BrowserControlParams,
            payload,
            default_timeout_sec=default_timeout_sec,
            max_timeout_sec=max_timeout_sec,
        )
        return params

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = BrowserControlParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            result_payload = await self._execute_action(ctx=ctx, payload=payload)
            if self._should_persist_session_state(payload.action):
                await self._sessions.persist_session_state(
                    root_dir=self._settings.root_dir,
                    profile_id=ctx.profile_id,
                    session_id=ctx.session_id,
                )
            return ToolResult(ok=True, payload=result_payload)
        except _BrowserSessionNotOpenError as exc:
            return await self._build_error_result(
                ctx=ctx,
                error_code="browser_session_not_open",
                reason=str(exc),
            )
        except _BrowserUnavailableError as exc:
            return await self._build_error_result(
                ctx=ctx,
                error_code="browser_unavailable",
                reason=str(exc),
            )
        except ValueError as exc:
            return await self._build_error_result(
                ctx=ctx,
                error_code="browser_invalid",
                reason=str(exc),
            )
        except PlaywrightTimeoutError:
            return await self._build_error_result(
                ctx=ctx,
                error_code="browser_action_failed",
                reason=f"Browser action timed out after {payload.timeout_ms} ms",
            )
        except PlaywrightError as exc:
            return await self._build_error_result(
                ctx=ctx,
                error_code="browser_action_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return await self._build_error_result(
                ctx=ctx,
                error_code="browser_action_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )

    @staticmethod
    def _should_persist_session_state(action: str) -> bool:
        """Persist storage only after browser actions likely to change session/page state."""

        return action in {"open", "navigate", "click", "fill", "press", "select", "check"}

    async def _build_error_result(
        self,
        *,
        ctx: ToolContext,
        error_code: str,
        reason: str,
    ) -> ToolResult:
        metadata = _browser_error_metadata(error_code=error_code, reason=reason)
        if metadata.get("requires_session_reset") is True:
            await self._sessions.close_session(
                root_dir=self._settings.root_dir,
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
            )
        return ToolResult.error(
            error_code=error_code,
            reason=reason,
            metadata=metadata,
        )

    async def _execute_action(
        self,
        *,
        ctx: ToolContext,
        payload: BrowserControlParams,
    ) -> dict[str, object]:
        action = payload.action
        if action == "open":
            return await self._open(ctx=ctx, payload=payload)
        if action == "navigate":
            return await self._navigate(ctx=ctx, payload=payload)
        if action == "click":
            return await self._click(ctx=ctx, payload=payload)
        if action == "fill":
            return await self._fill(ctx=ctx, payload=payload)
        if action == "press":
            return await self._press(ctx=ctx, payload=payload)
        if action == "select":
            return await self._select(ctx=ctx, payload=payload)
        if action == "check":
            return await self._check(ctx=ctx, payload=payload)
        if action == "scroll":
            return await self._scroll(ctx=ctx, payload=payload)
        if action == "wait":
            return await self._wait(ctx=ctx, payload=payload)
        if action == "content":
            return await self._content(ctx=ctx, payload=payload)
        if action == "snapshot":
            return await self._snapshot(ctx=ctx, payload=payload)
        if action == "screenshot":
            return await self._screenshot(ctx=ctx, payload=payload)
        if action == "close":
            return await self._close(ctx=ctx, payload=payload)
        raise ValueError(f"Unsupported action: {action}")

    async def _open(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        try:
            session, reused = await self._sessions.open_or_reuse(
                root_dir=self._settings.root_dir,
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
                headless=self._settings.browser_headless,
                idle_ttl_sec=self._settings.browser_session_idle_ttl_sec,
                open_browser=self._open_browser,
                backend_name=self._settings.browser_backend,
                backend_identity=self._browser_backend_identity(),
            )
        except _BrowserUnavailableError:
            raise
        except Exception as exc:
            raise _BrowserUnavailableError(
                f"Failed to launch browser: {exc.__class__.__name__}. {_browser_install_hint(self._settings)}"
            ) from exc

        if payload.url is not None:
            await self._goto_page(
                page=session.page,
                url=payload.url,
                timeout_ms=payload.timeout_ms,
                wait_until=payload.wait_until,
            )

        return {
            "action": "open",
            "opened": True,
            "reused": reused,
            "storage_state_loaded": session.storage_state_loaded,
            "storage_state_path": self._to_relative(session.storage_state_path),
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _navigate(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        if payload.url is None:
            raise ValueError("url is required for navigate action")
        session = await self._require_session(ctx)
        await self._goto_page(
            page=session.page,
            url=payload.url,
            timeout_ms=payload.timeout_ms,
            wait_until=payload.wait_until,
        )
        return {
            "action": "navigate",
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _click(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        if not self._has_target(payload):
            raise ValueError(
                "click action requires one browser target: selector, label, placeholder, "
                "field_name, role, or target_text"
            )
        session = await self._require_session(ctx)
        if payload.selector is not None:
            await session.page.click(payload.selector, timeout=payload.timeout_ms)
        else:
            locator = self._require_target_locator(session.page, payload=payload, action="click")
            await locator.click(timeout=payload.timeout_ms)
        return {
            "action": "click",
            "selector": payload.selector,
            "target": self._target_descriptor(payload),
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _fill(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        if payload.text is None:
            raise ValueError("text is required for fill action")
        if not self._has_target(payload):
            raise ValueError(
                "fill action requires one browser target: selector, label, placeholder, "
                "field_name, role, or target_text"
            )
        session = await self._require_session(ctx)
        if payload.selector is not None:
            await session.page.fill(payload.selector, payload.text, timeout=payload.timeout_ms)
        else:
            locator = self._require_target_locator(session.page, payload=payload, action="fill")
            await locator.fill(payload.text, timeout=payload.timeout_ms)
        return {
            "action": "fill",
            "selector": payload.selector,
            "target": self._target_descriptor(payload),
            "chars": len(payload.text),
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _press(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        if payload.key is None:
            raise ValueError("key is required for press action")
        session = await self._require_session(ctx)
        locator = self._maybe_target_locator(session.page, payload=payload, action="press")
        if locator is not None:
            await locator.press(payload.key, timeout=payload.timeout_ms)
        else:
            keyboard = getattr(session.page, "keyboard", None)
            press = None if keyboard is None else getattr(keyboard, "press", None)
            if not callable(press):
                raise ValueError(
                    "press action without a target requires page keyboard support; "
                    "provide selector/label/placeholder/role/target_text or use a compatible runtime"
                )
            await press(payload.key)
        return {
            "action": "press",
            "target": self._target_descriptor(payload),
            "key": payload.key,
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _select(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        if not self._has_target(payload):
            raise ValueError(
                "select action requires one browser target: selector, label, placeholder, "
                "field_name, role, or target_text"
            )
        session = await self._require_session(ctx)
        locator = self._require_target_locator(session.page, payload=payload, action="select")
        option_value = self._clean_text(payload.value)
        option_label = self._clean_text(self._preferred_target_text(payload))
        if not option_value and not option_label:
            raise ValueError("select action requires either value or target_text")
        kwargs: dict[str, object] = {"timeout": payload.timeout_ms}
        if option_value:
            kwargs["value"] = option_value
        elif option_label:
            kwargs["label"] = option_label
        await locator.select_option(**kwargs)
        return {
            "action": "select",
            "target": self._target_descriptor(payload),
            "value": option_value or option_label,
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _check(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        if not self._has_target(payload):
            raise ValueError(
                "check action requires one browser target: selector, label, placeholder, "
                "field_name, role, or target_text"
            )
        session = await self._require_session(ctx)
        locator = self._require_target_locator(session.page, payload=payload, action="check")
        await locator.check(timeout=payload.timeout_ms)
        return {
            "action": "check",
            "target": self._target_descriptor(payload),
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _scroll(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        session = await self._require_session(ctx)
        locator = self._maybe_target_locator(session.page, payload=payload, action="scroll")
        if locator is not None:
            await locator.scroll_into_view_if_needed(timeout=payload.timeout_ms)
            return {
                "action": "scroll",
                "selector": payload.selector,
                "target": self._target_descriptor(payload),
                "url": str(getattr(session.page, "url", "") or ""),
            }
        await session.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return {
            "action": "scroll",
            "position": "bottom",
            "url": str(getattr(session.page, "url", "") or ""),
        }

    async def _wait(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        session = await self._require_session(ctx)
        wait_target = "timeout"
        if payload.url is not None:
            await session.page.wait_for_url(
                payload.url,
                timeout=payload.timeout_ms,
                wait_until=payload.wait_until,
            )
            wait_target = "url"
        elif payload.selector is not None:
            await session.page.wait_for_selector(
                payload.selector,
                timeout=payload.timeout_ms,
                state=payload.state,
            )
            wait_target = "selector"
        elif (locator := self._maybe_target_locator(session.page, payload=payload, action="wait")) is not None:
            await locator.wait_for(timeout=payload.timeout_ms, state=payload.state)
            wait_target = "target"
        elif payload.text is not None:
            await session.page.wait_for_function(
                "(needle) => document.body && document.body.innerText && document.body.innerText.includes(needle)",
                payload.text,
                timeout=payload.timeout_ms,
            )
            wait_target = "text"
        else:
            await session.page.wait_for_timeout(payload.timeout_ms)
        return {
            "action": "wait",
            "wait_target": wait_target,
            "selector": payload.selector,
            "target": self._target_descriptor(payload),
            "state": payload.state,
            "text": payload.text,
            "url": str(getattr(session.page, "url", "") or payload.url or ""),
            "timeout_ms": payload.timeout_ms,
        }

    async def _content(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        session = await self._require_session(ctx)
        content = str(await session.page.content())
        html_excerpt, html_truncated = self._truncate_raw_text(content, limit=payload.max_chars)
        snapshot = await capture_browser_page_snapshot(session.page, max_chars=payload.max_chars)
        body_text = str(snapshot.get("body_text") or "")
        body_text_truncated = bool(snapshot.get("body_text_truncated"))
        return {
            "action": "content",
            "url": str(getattr(session.page, "url", "") or ""),
            "title": snapshot.get("title") or "",
            "content": html_excerpt,
            "truncated": html_truncated,
            "text": body_text,
            "text_truncated": body_text_truncated,
            "headings": snapshot.get("headings") or [],
            "interactives": snapshot.get("interactives") or [],
        }

    async def _snapshot(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        session = await self._require_session(ctx)
        html = str(await session.page.content())
        snapshot = await capture_browser_page_snapshot(session.page, max_chars=payload.max_chars)
        text_excerpt = str(snapshot.get("body_text") or "")
        text_truncated = bool(snapshot.get("body_text_truncated"))
        html_excerpt, html_truncated = self._truncate_raw_text(html, limit=payload.max_chars)
        files = self._resolve_snapshot_paths(ctx=ctx, requested_path=payload.path)
        files["screenshot"].parent.mkdir(parents=True, exist_ok=True)
        await session.page.screenshot(
            path=str(files["screenshot"]),
            full_page=payload.full_page,
            timeout=payload.timeout_ms,
        )
        files["text"].write_text(text_excerpt, encoding="utf-8")
        files["html"].write_text(html_excerpt, encoding="utf-8")
        files["json"].write_text(
            json.dumps(
                {
                    "url": str(getattr(session.page, "url", "") or ""),
                    "title": snapshot.get("title") or "",
                    "headings": snapshot.get("headings") or [],
                    "buttons": snapshot.get("buttons") or [],
                    "links": snapshot.get("links") or [],
                    "forms": snapshot.get("forms") or [],
                    "images": snapshot.get("images") or [],
                    "interactives": snapshot.get("interactives") or [],
                    "text": text_excerpt,
                    "text_truncated": text_truncated,
                    "html_truncated": html_truncated,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return {
            "action": "snapshot",
            "url": str(getattr(session.page, "url", "") or ""),
            "title": snapshot.get("title") or "",
            "snapshot": {
                "headings": snapshot.get("headings") or [],
                "buttons": snapshot.get("buttons") or [],
                "links": snapshot.get("links") or [],
                "forms": snapshot.get("forms") or [],
                "images": snapshot.get("images") or [],
                "interactives": snapshot.get("interactives") or [],
                "text": text_excerpt,
                "text_truncated": text_truncated,
                "html_excerpt": html_excerpt,
                "html_truncated": html_truncated,
            },
            "artifact": {
                "kind": "browser_snapshot",
                "files": {name: self._to_relative(path) for name, path in files.items()},
            },
        }

    async def _screenshot(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        session = await self._require_session(ctx)
        path = self._resolve_screenshot_path(ctx=ctx, requested_path=payload.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await session.page.screenshot(
            path=str(path),
            full_page=payload.full_page,
            timeout=payload.timeout_ms,
        )
        return {
            "action": "screenshot",
            "path": self._to_relative(path),
        }

    async def _close(self, *, ctx: ToolContext, payload: BrowserControlParams) -> dict[str, object]:
        closed = await self._sessions.close_session(
            root_dir=self._settings.root_dir,
            profile_id=ctx.profile_id,
            session_id=ctx.session_id,
            clear_persisted_state=payload.clear_state,
        )
        return {
            "action": "close",
            "closed": closed,
            "clear_state": payload.clear_state,
        }

    async def _goto_page(
        self,
        *,
        page: Any,
        url: str,
        timeout_ms: int,
        wait_until: str,
    ) -> None:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only http/https URLs are allowed")
        await page.goto(url, timeout=timeout_ms, wait_until=wait_until)

    async def _start_playwright(self) -> Any:
        if _PLAYWRIGHT_FACTORY is None:
            reason = "Playwright is not installed"
            if _PLAYWRIGHT_IMPORT_ERROR is not None:
                reason = f"{reason}: {_PLAYWRIGHT_IMPORT_ERROR.__class__.__name__}"
            raise _BrowserUnavailableError(f"{reason}. {_browser_install_hint(self._settings)}")
        manager = _PLAYWRIGHT_FACTORY()
        if manager is None:
            raise _BrowserUnavailableError(
                f"Playwright runtime is unavailable. {_browser_install_hint(self._settings)}"
            )
        try:
            return await manager.start()
        except Exception as exc:
            raise _BrowserUnavailableError(
                f"Failed to start Playwright: {exc.__class__.__name__}. {_browser_install_hint(self._settings)}"
            ) from exc

    async def _open_browser(self, headless: bool) -> tuple[Any, Any]:
        playwright = await self._start_playwright()
        browser_type = getattr(playwright, "chromium", None)
        if browser_type is None:
            try:
                await playwright.stop()
            except Exception:
                pass
            raise _BrowserUnavailableError(
                f"Playwright chromium browser type is unavailable. {_browser_install_hint(self._settings)}"
            )

        if self._settings.browser_backend == LIGHTPANDA_CDP:
            cdp_url = (self._settings.browser_cdp_url or "").strip()
            if not cdp_url:
                try:
                    await playwright.stop()
                except Exception:
                    pass
                raise _BrowserUnavailableError(
                    "Browser runtime is not configured for Lightpanda. "
                    f"Run `afk browser install` or set `afk browser cdp-url {LIGHTPANDA_DEFAULT_CDP_URL}` first."
                )
            connect_over_cdp = getattr(browser_type, "connect_over_cdp", None)
            if not callable(connect_over_cdp):
                try:
                    await playwright.stop()
                except Exception:
                    pass
                raise _BrowserUnavailableError(
                    "Current Playwright runtime does not support CDP connections. "
                    f"{_browser_install_hint(self._settings)}"
                )
            try:
                browser = await connect_over_cdp(cdp_url)
            except Exception:
                try:
                    await playwright.stop()
                except Exception:
                    pass
                raise
            return playwright, browser

        try:
            browser = await browser_type.launch(headless=headless)
        except Exception:
            try:
                await playwright.stop()
            except Exception:
                pass
            raise
        return playwright, browser

    async def _require_session(self, ctx: ToolContext) -> _BrowserSession:
        session = await self._sessions.get(
            root_dir=self._settings.root_dir,
            profile_id=ctx.profile_id,
            session_id=ctx.session_id,
            idle_ttl_sec=self._settings.browser_session_idle_ttl_sec,
        )
        if session is None:
            raise _BrowserSessionNotOpenError(
                "Browser session is not open; call action='open' first"
            )
        return _BrowserSession(
            playwright=session.playwright,
            browser=session.browser,
            page=session.page,
        )

    def _browser_backend_identity(self) -> str:
        backend = self._settings.browser_backend
        if backend == LIGHTPANDA_CDP:
            return f"{backend}:{(self._settings.browser_cdp_url or '').strip()}"
        return backend

    def _resolve_screenshot_path(self, *, ctx: ToolContext, requested_path: str | None) -> Path:
        root = self._settings.root_dir.resolve()
        if requested_path is None:
            safe_session = re.sub(r"[^a-zA-Z0-9_.-]+", "-", ctx.session_id).strip("-") or "session"
            return root / "tmp" / f"browser-{safe_session}.png"

        candidate = Path(requested_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("screenshot path must stay within workspace root") from exc
        return resolved

    def _resolve_snapshot_paths(self, *, ctx: ToolContext, requested_path: str | None) -> dict[str, Path]:
        root = self._settings.root_dir.resolve()
        if requested_path is None:
            safe_session = re.sub(r"[^a-zA-Z0-9_.-]+", "-", ctx.session_id).strip("-") or "session"
            stem = f"snapshot-{int(time.time() * 1000)}"
            screenshot = root / "tmp" / "browser" / safe_session / f"{stem}.png"
            base = screenshot.with_suffix("")
        else:
            requested = self._resolve_screenshot_path(ctx=ctx, requested_path=requested_path)
            if requested.suffix.lower() == ".png":
                screenshot = requested
                base = screenshot.with_suffix("")
            elif requested.suffix:
                base = requested.with_suffix("")
                screenshot = base.with_suffix(".png")
            else:
                base = requested
                screenshot = requested.with_suffix(".png")
        return {
            "screenshot": screenshot,
            "html": base.with_suffix(".html"),
            "text": base.with_suffix(".txt"),
            "json": base.with_suffix(".json"),
        }

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path.resolve())

    def _maybe_target_locator(
        self,
        page: Any,
        *,
        payload: BrowserControlParams,
        action: str,
    ) -> Any | None:
        del action
        if payload.selector is not None:
            return page.locator(payload.selector)
        if payload.label is not None:
            getter = getattr(page, "get_by_label", None)
            if callable(getter):
                return getter(payload.label, exact=payload.exact)
        if payload.placeholder is not None:
            getter = getattr(page, "get_by_placeholder", None)
            if callable(getter):
                return getter(payload.placeholder, exact=payload.exact)
        if payload.field_name is not None:
            return page.locator(self._attribute_selector("name", payload.field_name))
        preferred_target_text = self._preferred_target_text(payload)
        if payload.role is not None:
            getter = getattr(page, "get_by_role", None)
            if not callable(getter):
                raise ValueError("role targeting is not supported by the current browser runtime")
            kwargs: dict[str, object] = {}
            if preferred_target_text:
                kwargs["name"] = preferred_target_text
                kwargs["exact"] = payload.exact
            return getter(payload.role, **kwargs)
        if preferred_target_text:
            getter = getattr(page, "get_by_text", None)
            if callable(getter):
                return getter(preferred_target_text, exact=payload.exact)
        return None

    def _require_target_locator(
        self,
        page: Any,
        *,
        payload: BrowserControlParams,
        action: str,
    ) -> Any:
        locator = self._maybe_target_locator(page, payload=payload, action=action)
        if locator is not None:
            return locator
        raise ValueError(
            f"{action} action requires one browser target: selector, label, placeholder, "
            "field_name, role, or target_text"
        )

    @classmethod
    def _preferred_target_text(cls, payload: BrowserControlParams) -> str:
        direct = cls._clean_text(payload.target_text)
        if direct:
            return direct
        action = payload.action
        if action in {"click", "press", "select", "check"}:
            return cls._clean_text(payload.text)
        return ""

    @classmethod
    def _target_descriptor(cls, payload: BrowserControlParams) -> dict[str, object]:
        descriptor: dict[str, object] = {}
        if payload.selector is not None:
            descriptor["selector"] = payload.selector
        if payload.label is not None:
            descriptor["label"] = payload.label
        if payload.placeholder is not None:
            descriptor["placeholder"] = payload.placeholder
        if payload.field_name is not None:
            descriptor["field_name"] = payload.field_name
        if payload.role is not None:
            descriptor["role"] = payload.role
        preferred_text = cls._preferred_target_text(payload)
        if preferred_text:
            descriptor["target_text"] = preferred_text
        descriptor["exact"] = payload.exact
        return descriptor

    @staticmethod
    def _attribute_selector(attribute: str, value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'[{attribute}="{escaped}"]'

    @classmethod
    def _has_target(cls, payload: BrowserControlParams) -> bool:
        return bool(
            payload.selector is not None
            or payload.label is not None
            or payload.placeholder is not None
            or payload.field_name is not None
            or payload.role is not None
            or cls._preferred_target_text(payload)
        )

    @staticmethod
    def _clean_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _truncate_text(cls, value: str, *, limit: int) -> tuple[str, bool]:
        cleaned = cls._clean_text(value)
        if len(cleaned) <= limit:
            return cleaned, False
        clipped = cleaned[:limit].rstrip()
        return clipped, True

    @classmethod
    def _truncate_raw_text(cls, value: str, *, limit: int) -> tuple[str, bool]:
        if len(value) <= limit:
            return value, False
        return value[:limit], True


def create_tool(settings: Settings) -> ToolBase:
    """Create browser.control tool instance."""

    return BrowserControlTool(settings=settings)
