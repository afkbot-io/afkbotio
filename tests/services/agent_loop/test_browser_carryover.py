"""Tests for browser carryover prompt compaction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from afkbot.services.agent_loop import browser_carryover as browser_carryover_module
from afkbot.services.agent_loop.browser_carryover import BrowserCarryoverService
from afkbot.settings import Settings


def test_browser_carryover_truncates_on_bullet_boundaries(tmp_path) -> None:
    """Carryover truncation should avoid cutting one bullet line mid-sentence."""

    service = BrowserCarryoverService(
        settings=Settings(root_dir=tmp_path),
        runlog_repo=object(),  # type: ignore[arg-type]
        max_chars=200,
    )

    summary = "\n".join(
        [
            "Trusted live browser carryover from the current runtime.",
            "- Browser session status: open in the current runtime and should be reused instead of reopening the site unless recovery is required.",
            "- Live page URL: https://example.com/dashboard?tab=orders&view=expanded&filter=high-priority",
        ]
    )

    clipped = service._truncate_summary(summary)

    assert clipped.endswith("\n- Carryover text truncated.")
    assert "- Browser session status: open in the current runtime." not in clipped
    assert "- Live page URL: https://example.com/dashboard" not in clipped


@pytest.mark.asyncio
async def test_browser_carryover_reads_live_session_without_touching_ttl(tmp_path) -> None:
    """Live carryover should inspect browser state without extending the session lifetime."""

    class _FakeRunlogRepo:
        async def list_session_events(self, **_: object) -> list[object]:
            return []

    class _RecordingSessionManager:
        def __init__(self) -> None:
            self.touch_values: list[bool] = []

        async def get(self, **kwargs: object) -> None:
            self.touch_values.append(bool(kwargs["touch"]))
            return None

    session_manager = _RecordingSessionManager()
    service = BrowserCarryoverService(
        settings=Settings(root_dir=tmp_path),
        runlog_repo=_FakeRunlogRepo(),  # type: ignore[arg-type]
        session_manager=session_manager,  # type: ignore[arg-type]
    )

    assert await service.build_prompt_block(profile_id="default", session_id="s-1") is None
    assert session_manager.touch_values == [False]


@pytest.mark.asyncio
async def test_browser_carryover_reuses_cached_live_summary_until_page_changes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live carryover should reuse the cached snapshot for steady pages and refresh on URL change."""

    class _FakeRunlogRepo:
        async def list_session_events(self, **_: object) -> list[object]:
            return []

    class _FakeSessionManager:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        async def get(self, **_: object) -> object:
            return self._handle

    page = SimpleNamespace(url="https://example.com/first")
    handle = SimpleNamespace(
        page=page,
        live_carryover_summary=None,
        live_carryover_page_url="",
        live_carryover_updated_monotonic=0.0,
    )
    calls: list[str] = []

    async def _fake_capture(page_obj: object, *, max_chars: int) -> dict[str, object]:
        _ = max_chars
        url = str(getattr(page_obj, "url", "") or "")
        calls.append(url)
        return {
            "url": url,
            "title": f"title:{url.rsplit('/', 1)[-1]}",
            "headings": ["Dashboard"],
            "buttons": [],
            "links": [],
            "body_text": "Visible page text",
        }

    monkeypatch.setattr(browser_carryover_module, "capture_browser_page_snapshot", _fake_capture)

    service = BrowserCarryoverService(
        settings=Settings(root_dir=tmp_path),
        runlog_repo=_FakeRunlogRepo(),  # type: ignore[arg-type]
        session_manager=_FakeSessionManager(handle),  # type: ignore[arg-type]
        live_refresh_window_sec=60,
    )

    first = await service.build_prompt_block(profile_id="default", session_id="s-1")
    second = await service.build_prompt_block(profile_id="default", session_id="s-1")
    page.url = "https://example.com/second"
    third = await service.build_prompt_block(profile_id="default", session_id="s-1")

    assert first == second
    assert "https://example.com/first" in first
    assert "https://example.com/second" in third
    assert calls == ["https://example.com/first", "https://example.com/second"]
