"""Shared in-process browser session manager for Playwright-backed tools."""

from __future__ import annotations

import asyncio
import re
from inspect import isawaitable
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from afkbot.browser_backends import PLAYWRIGHT_CHROMIUM


_SessionKey = tuple[str, str, str]


@dataclass(slots=True)
class BrowserSessionHandle:
    """One live browser session bound to workspace/profile/chat session."""

    root_key: str
    profile_id: str
    session_id: str
    playwright: Any
    browser: Any
    context: Any | None
    page: Any
    storage_state_path: Path
    storage_state_loaded: bool
    backend_name: str
    backend_identity: str
    headless: bool
    created_monotonic: float
    last_used_monotonic: float


class BrowserSessionManager:
    """Keep browser sessions alive across turns within one Python process."""

    def __init__(self) -> None:
        self._index_lock = asyncio.Lock()
        self._session_locks: MutableMapping[_SessionKey, asyncio.Lock] = {}
        self._sessions: MutableMapping[_SessionKey, BrowserSessionHandle] = {}

    async def open_or_reuse(
        self,
        *,
        root_dir: Path,
        profile_id: str,
        session_id: str,
        headless: bool,
        idle_ttl_sec: int,
        start_playwright: Callable[[], Awaitable[Any]] | None = None,
        open_browser: Callable[[bool], Awaitable[tuple[Any, Any]]] | None = None,
        backend_name: str = PLAYWRIGHT_CHROMIUM,
        backend_identity: str | None = None,
    ) -> tuple[BrowserSessionHandle, bool]:
        """Return existing live session for key or create a new one."""

        await self.close_idle_sessions(root_dir=root_dir, idle_ttl_sec=idle_ttl_sec)
        key = self._make_key(root_dir=root_dir, profile_id=profile_id, session_id=session_id)
        resolved_backend_identity = (backend_identity or backend_name).strip() or backend_name
        lock = await self._get_session_lock(key)
        async with lock:
            existing = self._sessions.get(key)
            if existing is not None:
                if not await self._handle_is_alive(existing):
                    await self._close_handle(existing)
                    self._sessions.pop(key, None)
                    existing = None
            if existing is not None:
                if (
                    existing.headless == headless
                    and existing.backend_identity == resolved_backend_identity
                ):
                    self._touch(existing)
                    return existing, True
                await self._close_handle(existing)
                self._sessions.pop(key, None)

            if open_browser is None:
                if start_playwright is None:
                    raise RuntimeError("browser session requires open_browser or start_playwright")

                async def _default_open_browser(current_headless: bool) -> tuple[Any, Any]:
                    playwright = await start_playwright()
                    return playwright, await playwright.chromium.launch(headless=current_headless)

                open_browser = _default_open_browser

            playwright = None
            browser = None
            context = None
            storage_state_path = self.storage_state_path(
                root_dir=root_dir,
                profile_id=profile_id,
                session_id=session_id,
            )
            try:
                playwright, browser = await open_browser(headless)
                context, page, storage_state_loaded = await self._open_page_with_context(
                    browser=browser,
                    storage_state_path=storage_state_path,
                )
            except Exception:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
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
                raise

            now = time.monotonic()
            handle = BrowserSessionHandle(
                root_key=key[0],
                profile_id=profile_id,
                session_id=session_id,
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
                storage_state_path=storage_state_path,
                storage_state_loaded=storage_state_loaded,
                backend_name=backend_name,
                backend_identity=resolved_backend_identity,
                headless=headless,
                created_monotonic=now,
                last_used_monotonic=now,
            )
            self._sessions[key] = handle
            return handle, False

    async def get(
        self,
        *,
        root_dir: Path,
        profile_id: str,
        session_id: str,
        idle_ttl_sec: int,
    ) -> BrowserSessionHandle | None:
        """Return current live session for key, or `None` when absent/expired."""

        await self.close_idle_sessions(root_dir=root_dir, idle_ttl_sec=idle_ttl_sec)
        key = self._make_key(root_dir=root_dir, profile_id=profile_id, session_id=session_id)
        lock = await self._get_session_lock(key)
        async with lock:
            handle = self._sessions.get(key)
            if handle is None:
                return None
            if not await self._handle_is_alive(handle):
                current = self._sessions.get(key)
                if current is handle:
                    self._sessions.pop(key, None)
                    await self._close_handle(handle)
                return None
            self._touch(handle)
            return handle

    async def close_session(
        self,
        *,
        root_dir: Path,
        profile_id: str,
        session_id: str,
        clear_persisted_state: bool = False,
    ) -> bool:
        """Close one live browser session if present."""

        key = self._make_key(root_dir=root_dir, profile_id=profile_id, session_id=session_id)
        lock = await self._get_session_lock(key)
        async with lock:
            handle = self._sessions.pop(key, None)
            if handle is not None:
                await self._close_handle(handle, clear_persisted_state=clear_persisted_state)
                return True
            if clear_persisted_state:
                return await self.clear_persisted_state(
                    root_dir=root_dir,
                    profile_id=profile_id,
                    session_id=session_id,
                )
            return False

    async def close_all_for_root(self, *, root_dir: Path, clear_persisted_state: bool = False) -> int:
        """Close every live browser session for one workspace root."""

        root_key = str(root_dir.resolve())
        keys = [key for key in list(self._sessions) if key[0] == root_key]
        closed = 0
        for key in keys:
            lock = await self._get_session_lock(key)
            async with lock:
                handle = self._sessions.pop(key, None)
                if handle is not None:
                    await self._close_handle(handle, clear_persisted_state=clear_persisted_state)
            if handle is None:
                continue
            closed += 1
        if clear_persisted_state:
            state_root = self.state_root_dir(root_dir=root_dir)
            if state_root.exists():
                for path in sorted(state_root.rglob("*"), reverse=True):
                    try:
                        if path.is_file():
                            path.unlink()
                        else:
                            path.rmdir()
                    except OSError:
                        continue
                try:
                    state_root.rmdir()
                except OSError:
                    pass
        return closed

    async def close_idle_sessions(self, *, root_dir: Path, idle_ttl_sec: int) -> int:
        """Close expired sessions for one workspace root based on idle TTL."""

        root_key = str(root_dir.resolve())
        now = time.monotonic()
        expired_keys = [
            key
            for key, handle in self._sessions.items()
            if key[0] == root_key and (now - handle.last_used_monotonic) >= idle_ttl_sec
        ]
        closed = 0
        for key in expired_keys:
            lock = await self._get_session_lock(key)
            async with lock:
                handle = self._sessions.get(key)
                if handle is None:
                    continue
                if (now - handle.last_used_monotonic) < idle_ttl_sec:
                    continue
                self._sessions.pop(key, None)
                await self._close_handle(handle)
                closed += 1
        return closed

    async def persist_session_state(
        self,
        *,
        root_dir: Path,
        profile_id: str,
        session_id: str,
    ) -> bool:
        """Persist storage state for one live session when browser context supports it."""

        key = self._make_key(root_dir=root_dir, profile_id=profile_id, session_id=session_id)
        lock = await self._get_session_lock(key)
        async with lock:
            handle = self._sessions.get(key)
            if handle is None:
                return False
            return await self._persist_handle_state(handle)

    async def clear_persisted_state(
        self,
        *,
        root_dir: Path,
        profile_id: str,
        session_id: str,
    ) -> bool:
        """Delete persisted browser storage state for one session, if present."""

        path = self.storage_state_path(
            root_dir=root_dir,
            profile_id=profile_id,
            session_id=session_id,
        )
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        self._prune_empty_parent_dirs(path.parent, stop_at=self.state_root_dir(root_dir=root_dir))
        return True

    @classmethod
    def state_root_dir(cls, *, root_dir: Path) -> Path:
        """Return workspace-local directory for persisted browser state."""

        return root_dir.resolve() / "tmp" / "browser-state"

    @classmethod
    def storage_state_path(
        cls,
        *,
        root_dir: Path,
        profile_id: str,
        session_id: str,
    ) -> Path:
        """Return deterministic persisted storage-state path for one session."""

        safe_profile = cls._safe_key_component(profile_id)
        safe_session = cls._safe_key_component(session_id)
        return (
            cls.state_root_dir(root_dir=root_dir)
            / safe_profile
            / safe_session
            / "storage-state.json"
        )

    async def _get_session_lock(self, key: _SessionKey) -> asyncio.Lock:
        async with self._index_lock:
            lock = self._session_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[key] = lock
            return lock

    @staticmethod
    def _make_key(*, root_dir: Path, profile_id: str, session_id: str) -> _SessionKey:
        return (str(root_dir.resolve()), profile_id, session_id)

    @staticmethod
    def _safe_key_component(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip()).strip("-")
        return normalized or "session"

    @staticmethod
    def _touch(handle: BrowserSessionHandle) -> None:
        handle.last_used_monotonic = time.monotonic()

    @classmethod
    async def _handle_is_alive(cls, handle: BrowserSessionHandle) -> bool:
        try:
            if await cls._probe_page_closed(handle.page):
                return False
            if await cls._probe_browser_disconnected(handle.browser):
                return False
        except Exception:
            return False
        return True

    @staticmethod
    async def _probe_page_closed(page: Any) -> bool:
        probe = getattr(page, "is_closed", None)
        if probe is not None:
            result = probe() if callable(probe) else probe
            if isawaitable(result):
                result = await result
            if result is True:
                return True
        closed = getattr(page, "closed", None)
        return bool(closed is True)

    @staticmethod
    async def _probe_browser_disconnected(browser: Any) -> bool:
        probe = getattr(browser, "is_connected", None)
        if probe is not None:
            result = probe() if callable(probe) else probe
            if isawaitable(result):
                result = await result
            if result is False:
                return True
        connected = getattr(browser, "connected", None)
        if connected is not None:
            return connected is False
        return False

    @staticmethod
    async def _open_page_with_context(
        browser: Any,
        *,
        storage_state_path: Path,
    ) -> tuple[Any | None, Any, bool]:
        new_context = getattr(browser, "new_context", None)
        if callable(new_context):
            if storage_state_path.exists():
                try:
                    context = await new_context(storage_state=str(storage_state_path))
                except Exception:
                    try:
                        storage_state_path.unlink()
                    except OSError:
                        pass
                    context = await new_context()
                    loaded_from_storage = False
                else:
                    loaded_from_storage = True
            else:
                context = await new_context()
                loaded_from_storage = False
            context_new_page = getattr(context, "new_page", None)
            if callable(context_new_page):
                return context, await context_new_page(), loaded_from_storage
            browser_new_page = getattr(browser, "new_page", None)
            if callable(browser_new_page):
                return context, await browser_new_page(), loaded_from_storage
            raise RuntimeError("Browser context does not expose new_page")
        contexts = getattr(browser, "contexts", None)
        if isinstance(contexts, list) and contexts:
            context = contexts[0]
            context_new_page = getattr(context, "new_page", None)
            if callable(context_new_page):
                return context, await context_new_page(), False
            pages = getattr(context, "pages", None)
            if isinstance(pages, list) and pages:
                return context, pages[0], False
        browser_new_page = getattr(browser, "new_page", None)
        if callable(browser_new_page):
            return None, await browser_new_page(), False
        raise RuntimeError("Browser does not expose new_page")

    @staticmethod
    async def _close_handle(
        handle: BrowserSessionHandle,
        *,
        clear_persisted_state: bool = False,
    ) -> None:
        if clear_persisted_state:
            try:
                if handle.storage_state_path.exists():
                    handle.storage_state_path.unlink()
            except OSError:
                pass
        else:
            try:
                await BrowserSessionManager._persist_handle_state(handle)
            except Exception:
                pass
        try:
            await handle.page.close()
        except Exception:
            pass
        context = handle.context
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        try:
            await handle.browser.close()
        except Exception:
            pass
        try:
            await handle.playwright.stop()
        except Exception:
            pass
        if clear_persisted_state:
            BrowserSessionManager._prune_empty_parent_dirs(
                handle.storage_state_path.parent,
                stop_at=BrowserSessionManager.state_root_dir(root_dir=Path(handle.root_key)),
            )

    @staticmethod
    async def _persist_handle_state(handle: BrowserSessionHandle) -> bool:
        context = handle.context
        if context is None:
            context = getattr(handle.page, "context", None)
        if context is None:
            return False
        storage_state = getattr(context, "storage_state", None)
        if not callable(storage_state):
            return False
        handle.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        await storage_state(path=str(handle.storage_state_path))
        return True

    @staticmethod
    def _prune_empty_parent_dirs(path: Path, *, stop_at: Path) -> None:
        current = path
        resolved_stop = stop_at.resolve()
        while True:
            try:
                resolved_current = current.resolve()
            except OSError:
                break
            if resolved_current == resolved_stop:
                break
            try:
                current.rmdir()
            except OSError:
                break
            parent = current.parent
            if parent == current:
                break
            current = parent


_BROWSER_SESSION_MANAGER = BrowserSessionManager()


def get_browser_session_manager() -> BrowserSessionManager:
    """Return process-level browser session manager singleton."""

    return _BROWSER_SESSION_MANAGER
