"""Tests for shared browser session manager."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pytest import MonkeyPatch

from afkbot.services.browser_sessions import (
    get_browser_session_manager,
    reset_browser_session_manager_async,
)


class _FakePage:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False
        self.loaded_storage_state: str | None = None

    def is_connected(self) -> bool:
        return not self.closed

    async def new_context(self, *, storage_state: str | None = None) -> "_FakeContext":
        self.loaded_storage_state = storage_state
        return _FakeContext(self._page)

    async def new_page(self) -> _FakePage:
        return self._page

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False

    async def new_page(self) -> _FakePage:
        return self._page

    async def storage_state(self, *, path: str) -> None:
        Path(path).write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    async def close(self) -> None:
        self.closed = True


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser

    async def launch(self, *, headless: bool) -> _FakeBrowser:
        _ = headless
        return self._browser


async def test_browser_session_manager_reuses_same_session(tmp_path: Path) -> None:
    """Manager should return existing live session for identical root/profile/session key."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)
    launches = 0

    async def _start_playwright():  # type: ignore[no-untyped-def]
        nonlocal launches
        launches += 1
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    first, reused_first = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-1",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    second, reused_second = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-1",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    assert reused_first is False
    assert reused_second is True
    assert launches == 1
    assert first.page is second.page
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_closes_idle_sessions(tmp_path: Path) -> None:
    """Manager should reap idle sessions once TTL expires."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    handle, _ = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-idle",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    handle.last_used_monotonic -= 1000

    closed = await manager.close_idle_sessions(root_dir=tmp_path, idle_ttl_sec=1)

    assert closed == 1
    assert handle.page.closed is True
    assert handle.browser.closed is True
    assert handle.playwright.stopped is True
    assert await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-idle",
        idle_ttl_sec=600,
    ) is None


async def test_browser_session_manager_recreates_closed_page_handle(tmp_path: Path) -> None:
    """Manager should evict dead page handles and create a fresh session on reopen."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)
    launches = 0

    async def _start_playwright():  # type: ignore[no-untyped-def]
        nonlocal launches
        launches += 1
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    first, reused_first = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-dead",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    first.page.closed = True

    second, reused_second = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-dead",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    assert reused_first is False
    assert reused_second is False
    assert launches == 2
    assert first.page is not second.page
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_prunes_session_lock_after_close(tmp_path: Path) -> None:
    """Closing the last session with cleared state should release the process-lifetime lock entry."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-prune",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    key = (str(tmp_path.resolve()), "default", "s-prune")
    assert key in manager._session_locks

    assert (
        await manager.close_session(
            root_dir=tmp_path,
            profile_id="default",
            session_id="s-prune",
            clear_persisted_state=True,
        )
        is True
    )
    assert key not in manager._session_locks


async def test_browser_session_manager_prunes_miss_lock_without_persisted_state(tmp_path: Path) -> None:
    """Miss-path lookups should not keep per-session locks alive without a live handle or state file."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)

    key = (str(tmp_path.resolve()), "default", "s-miss-prune")
    await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-miss-prune",
        idle_ttl_sec=600,
    )

    assert key not in manager._session_locks

    closed = await manager.close_session(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-miss-prune",
    )

    assert closed is False
    assert key not in manager._session_locks


async def test_browser_session_manager_prunes_lock_after_persisted_only_cleanup(tmp_path: Path) -> None:
    """Persisted-state-only cleanup should delete the file and release the per-session lock."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)
    state_path = manager.storage_state_path(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persisted-only",
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{}", encoding="utf-8")

    closed = await manager.close_session(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persisted-only",
        clear_persisted_state=True,
    )

    key = (str(tmp_path.resolve()), "default", "s-persisted-only")
    assert closed is True
    assert not state_path.exists()
    assert key not in manager._session_locks


async def test_browser_session_manager_get_without_touch_keeps_idle_timestamp(tmp_path: Path) -> None:
    """Read-only lookups should not extend browser session lifetime."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    handle, _ = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-readonly",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    initial_last_used = handle.last_used_monotonic

    resolved = await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-readonly",
        idle_ttl_sec=600,
        touch=False,
    )

    assert resolved is handle
    assert handle.last_used_monotonic == initial_last_used
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_touch_invalidates_live_carryover_cache(tmp_path: Path) -> None:
    """Interactive browser access should invalidate cached live carryover state."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    handle, _ = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-cache",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    handle.live_carryover_summary = "cached summary"
    handle.live_carryover_page_url = "https://example.com"
    handle.live_carryover_updated_monotonic = 123.0

    resolved = await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-cache",
        idle_ttl_sec=600,
        touch=True,
    )

    assert resolved is handle
    assert handle.live_carryover_summary is None
    assert handle.live_carryover_page_url == ""
    assert handle.live_carryover_updated_monotonic == 0.0
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_enforces_idle_ttl_without_global_sweep(tmp_path: Path) -> None:
    """Local get/open checks should expire stale sessions even when throttled cleanup skips sweeping."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)
    launches = 0

    async def _start_playwright():  # type: ignore[no-untyped-def]
        nonlocal launches
        launches += 1
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    handle, reused_first = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-local-expire",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    root_key = str(tmp_path.resolve())
    manager._root_cleanup_deadlines[root_key] = float("inf")
    handle.last_used_monotonic -= 1000

    resolved = await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-local-expire",
        idle_ttl_sec=1,
    )

    assert reused_first is False
    assert resolved is None
    assert handle.page.closed is True
    assert handle.browser.closed is True
    assert handle.playwright.stopped is True

    reopened, reused_second = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-local-expire",
        headless=True,
        idle_ttl_sec=1,
        start_playwright=_start_playwright,
    )

    assert reused_second is False
    assert launches == 2
    assert reopened.page is not handle.page
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_get_closes_dead_handle_before_reopen(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Dead-handle cleanup in get() should hold the session lock until close completes."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)
    launches = 0
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    original_close_handle = type(manager)._close_handle

    async def _start_playwright():  # type: ignore[no-untyped-def]
        nonlocal launches
        launches += 1
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    async def _gated_close_handle(handle, *, clear_persisted_state: bool = False):  # type: ignore[no-untyped-def]
        close_started.set()
        await allow_close.wait()
        await original_close_handle(handle, clear_persisted_state=clear_persisted_state)

    monkeypatch.setattr(type(manager), "_close_handle", staticmethod(_gated_close_handle))

    first, reused_first = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-dead-get",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )
    first.page.closed = True

    get_task = asyncio.create_task(
        manager.get(
            root_dir=tmp_path,
            profile_id="default",
            session_id="s-dead-get",
            idle_ttl_sec=600,
        )
    )
    await close_started.wait()

    reopen_task = asyncio.create_task(
        manager.open_or_reuse(
            root_dir=tmp_path,
            profile_id="default",
            session_id="s-dead-get",
            headless=True,
            idle_ttl_sec=600,
            start_playwright=_start_playwright,
        )
    )
    await asyncio.sleep(0)
    assert launches == 1

    allow_close.set()
    assert await get_task is None
    second, reused_second = await reopen_task

    assert reused_first is False
    assert reused_second is False
    assert launches == 2
    assert first.page is not second.page
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_reopens_when_backend_identity_changes(
    tmp_path: Path,
) -> None:
    """Manager should recreate the session when the browser backend identity changes."""

    # Arrange
    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)
    launches = 0

    async def _open_browser(headless: bool) -> tuple[_FakePlaywright, _FakeBrowser]:
        _ = headless
        nonlocal launches
        launches += 1
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser), browser

    first, reused_first = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-backend",
        headless=True,
        idle_ttl_sec=600,
        open_browser=_open_browser,
        backend_name="playwright_chromium",
        backend_identity="playwright_chromium",
    )

    # Act
    second, reused_second = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-backend",
        headless=True,
        idle_ttl_sec=600,
        open_browser=_open_browser,
        backend_name="lightpanda_cdp",
        backend_identity="lightpanda_cdp:http://127.0.0.1:9222",
    )

    # Assert
    assert reused_first is False
    assert reused_second is False
    assert launches == 2
    assert first.browser.closed is True
    assert second.page is not first.page
    await manager.close_all_for_root(root_dir=tmp_path)


async def test_browser_session_manager_persists_and_reloads_storage_state(tmp_path: Path) -> None:
    """Manager should save storage state on close and reload it on the next open."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)
    browsers: list[_FakeBrowser] = []

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FakeBrowser(page)
        browsers.append(browser)
        return _FakePlaywright(browser)

    first, reused_first = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persist",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    state_path = manager.storage_state_path(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persist",
    )
    assert reused_first is False
    assert first.storage_state_loaded is False
    persisted = await manager.persist_session_state(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persist",
    )
    assert persisted is True
    assert state_path.exists()
    assert first.storage_state_loaded is False

    closed = await manager.close_session(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persist",
    )
    assert closed is True

    second, reused_second = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persist",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    assert reused_second is False
    assert second.storage_state_loaded is True
    assert len(browsers) == 2
    assert browsers[-1].loaded_storage_state == str(state_path)

    await manager.close_session(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-persist",
        clear_persisted_state=True,
    )
    assert not state_path.exists()


async def test_browser_session_manager_recovers_from_invalid_storage_state(tmp_path: Path) -> None:
    """Manager should drop invalid persisted state and reopen with a fresh browser context."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)
    state_path = manager.storage_state_path(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-invalid-state",
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{invalid json", encoding="utf-8")
    browser_calls: list[str | None] = []

    class _FallbackBrowser(_FakeBrowser):
        async def new_context(self, *, storage_state: str | None = None) -> _FakeContext:
            browser_calls.append(storage_state)
            if storage_state is not None:
                raise RuntimeError("invalid storage state")
            return _FakeContext(self._page)

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FallbackBrowser(page)
        return _FakePlaywright(browser)

    handle, reused = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-invalid-state",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    assert reused is False
    assert handle.storage_state_loaded is False
    assert browser_calls == [str(state_path), None]
    assert not state_path.exists()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)


async def test_reset_browser_session_manager_async_replaces_singleton(tmp_path: Path) -> None:
    """Reset hook should close existing sessions and replace the process singleton."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path)

    async def _start_playwright():  # type: ignore[no-untyped-def]
        page = _FakePage()
        browser = _FakeBrowser(page)
        return _FakePlaywright(browser)

    handle, _ = await manager.open_or_reuse(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-reset",
        headless=True,
        idle_ttl_sec=600,
        start_playwright=_start_playwright,
    )

    await reset_browser_session_manager_async()
    replacement = get_browser_session_manager()

    assert replacement is not manager
    assert handle.page.closed is True
    assert handle.browser.closed is True
    assert handle.playwright.stopped is True


async def test_reset_browser_session_manager_async_clears_known_persisted_state_roots(
    tmp_path: Path,
) -> None:
    """Reset with persisted-state cleanup should remove idle known roots from disk."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)
    await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-idle-state",
        idle_ttl_sec=600,
    )
    state_path = manager.storage_state_path(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-idle-state",
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{}", encoding="utf-8")

    await reset_browser_session_manager_async(clear_persisted_state=True)

    assert not state_path.exists()


async def test_browser_session_manager_prunes_known_root_without_live_or_persisted_state(
    tmp_path: Path,
) -> None:
    """Root tracking should not grow forever once a root has no sessions, locks, or persisted state."""

    manager = get_browser_session_manager()
    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)
    await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-known-root",
        idle_ttl_sec=600,
    )

    assert str(tmp_path.resolve()) in manager._known_root_keys

    await manager.close_all_for_root(root_dir=tmp_path, clear_persisted_state=True)

    assert str(tmp_path.resolve()) not in manager._known_root_keys
