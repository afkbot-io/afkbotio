"""Tests for web.search, web.fetch, and browser.control plugins."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pytest import MonkeyPatch

from afkbot.services.browser_sessions import get_browser_session_manager
from afkbot.services.tools.plugins.web_fetch import plugin as web_fetch_plugin
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


def _registry(settings: Settings) -> ToolRegistry:
    return ToolRegistry.from_plugins(
        ("web_search", "web_fetch", "browser_control"),
        settings=settings,
    )


async def test_web_search_success_with_brave_api(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """web.search should call Brave API and return normalized results."""

    settings = Settings(root_dir=tmp_path, brave_api_key="brave-key")
    registry = _registry(settings)
    tool = registry.get("web.search")
    assert tool is not None

    captured: dict[str, object] = {}

    class _FakeSearchClient:
        def __init__(self, *, timeout: object) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> "_FakeSearchClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            _ = exc_type, exc, tb

        async def get(
            self,
            url: str,
            *,
            params: dict[str, object],
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return httpx.Response(
                status_code=200,
                request=httpx.Request("GET", url),
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Example",
                                "url": "https://example.com/a",
                                "description": "Alpha",
                            },
                            {
                                "title": "Example 2",
                                "url": "https://example.com/b",
                                "snippet": "Beta",
                            },
                        ]
                    }
                },
            )

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.web_search.plugin.httpx.AsyncClient",
        _FakeSearchClient,
    )
    params = tool.parse_params(
        {
            "profile_key": "default",
            "query": "python",
            "count": 2,
            "lang": "en",
            "country": "us",
            "freshness": "pw",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)
    assert result.ok is True
    assert captured["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert captured["params"] == {
        "q": "python",
        "count": 2,
        "search_lang": "en",
        "country": "us",
        "freshness": "pw",
    }
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["X-Subscription-Token"] == "brave-key"
    assert result.payload["count"] == 2


async def test_web_search_returns_deterministic_errors(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """web.search should use deterministic error codes for missing key and request failures."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("web.search")
    assert tool is not None

    missing_key_params = tool.parse_params(
        {
            "profile_key": "default",
            "query": "python",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    missing_key_result = await tool.execute(
        ToolContext(profile_id="default", session_id="s", run_id=1),
        missing_key_params,
    )
    assert missing_key_result.ok is False
    assert missing_key_result.error_code == "web_search_api_key_missing"

    settings_with_key = Settings(root_dir=tmp_path, brave_api_key="brave-key")
    registry_with_key = _registry(settings_with_key)
    tool_with_key = registry_with_key.get("web.search")
    assert tool_with_key is not None

    class _FakeFailingSearchClient:
        def __init__(self, *, timeout: object) -> None:
            _ = timeout

        async def __aenter__(self) -> "_FakeFailingSearchClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            _ = exc_type, exc, tb

        async def get(
            self,
            url: str,
            *,
            params: dict[str, object],
            headers: dict[str, str],
        ) -> httpx.Response:
            _ = params, headers
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.web_search.plugin.httpx.AsyncClient",
        _FakeFailingSearchClient,
    )
    fail_params = tool_with_key.parse_params(
        {
            "profile_key": "default",
            "query": "python",
        },
        default_timeout_sec=settings_with_key.tool_timeout_default_sec,
        max_timeout_sec=settings_with_key.tool_timeout_max_sec,
    )
    fail_result = await tool_with_key.execute(
        ToolContext(profile_id="default", session_id="s", run_id=1),
        fail_params,
    )
    assert fail_result.ok is False
    assert fail_result.error_code == "web_search_failed"


async def test_web_fetch_extracts_readable_markdown(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """web.fetch should fetch HTML and return readable markdown with truncation metadata."""

    settings = Settings(root_dir=tmp_path, runtime_max_body_bytes=200_000)
    registry = _registry(settings)
    tool = registry.get("web.fetch")
    assert tool is not None
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.web_fetch.plugin.WebFetchTool._resolve_host_addresses",
        staticmethod(lambda *, host, port: ("93.184.216.34",)),
    )
    monkeypatch.setattr(
        web_fetch_plugin.WebFetchTool,
        "_open_url_sync",
        classmethod(
            lambda cls, **kwargs: web_fetch_plugin._FetchedPage(
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                url="https://example.com/page",
                body=(
                    b"<html><body><h1>Title</h1><p>Hello <b>world</b>.</p>"
                    b"<ul><li>first</li><li>second</li></ul></body></html>"
                ),
                truncated=False,
            )
        ),
    )
    params = tool.parse_params(
        {
            "profile_key": "default",
            "url": "https://example.com/page",
            "format": "markdown",
            "max_chars": 20,
            "max_bytes": 200_000,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)
    assert result.ok is True
    assert result.payload["format"] == "markdown"
    assert str(result.payload["content"]).startswith("# Title")
    assert result.payload["truncated_chars"] is True


async def test_web_fetch_returns_deterministic_errors(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """web.fetch should return deterministic invalid/failed error codes."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("web.fetch")
    assert tool is not None
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.web_fetch.plugin.WebFetchTool._resolve_host_addresses",
        staticmethod(lambda *, host, port: ("93.184.216.34",)),
    )

    invalid_params = tool.parse_params(
        {
            "profile_key": "default",
            "url": "file:///etc/hosts",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    invalid_result = await tool.execute(
        ToolContext(profile_id="default", session_id="s", run_id=1),
        invalid_params,
    )
    assert invalid_result.ok is False
    assert invalid_result.error_code == "web_fetch_invalid"

    monkeypatch.setattr(
        web_fetch_plugin.WebFetchTool,
        "_open_url_sync",
        classmethod(
            lambda cls, **kwargs: (_ for _ in ()).throw(
                web_fetch_plugin._WebFetchRequestError("ConnectError")
            )
        ),
    )
    fail_params = tool.parse_params(
        {
            "profile_key": "default",
            "url": "https://example.com",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    fail_result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), fail_params)
    assert fail_result.ok is False
    assert fail_result.error_code == "web_fetch_failed"


async def test_web_fetch_rejects_localhost_target(tmp_path: Path) -> None:
    """web.fetch should reject localhost URLs before network calls."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("web.fetch")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "url": "https://localhost/path",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)
    assert result.ok is False
    assert result.error_code == "web_fetch_invalid"
    assert "must not target localhost" in str(result.reason or "")


async def test_web_fetch_blocks_cross_host_redirect(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """web.fetch should reject redirects that change destination host."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("web.fetch")
    assert tool is not None
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.web_fetch.plugin.WebFetchTool._resolve_host_addresses",
        staticmethod(lambda *, host, port: ("93.184.216.34",)),
    )

    monkeypatch.setattr(
        web_fetch_plugin.WebFetchTool,
        "_open_url_sync",
        classmethod(
            lambda cls, **kwargs: web_fetch_plugin._FetchedPage(
                status_code=302,
                headers={"location": "https://evil.example/path"},
                url="https://example.com/redirect",
                body=b"",
                truncated=False,
            )
        ),
    )
    params = tool.parse_params(
        {
            "profile_key": "default",
            "url": "https://example.com/start",
            "format": "text",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)
    assert result.ok is False
    assert result.error_code == "web_fetch_invalid"
    assert "different host" in str(result.reason or "")


async def test_browser_control_full_action_flow(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """browser.control should support core navigation, inspection, and artifact actions."""

    settings = Settings(root_dir=tmp_path, browser_headless=False)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None
    launch_calls: list[bool] = []

    class _FakePage:
        def __init__(self) -> None:
            self.url = "about:blank"
            self.clicked: list[str] = []
            self.filled: list[tuple[str, str]] = []
            self.scrolled_selectors: list[str] = []
            self.scroll_to_bottom_calls = 0
            self.waited_for_selector: list[str] = []
            self.waited_for_text: list[str] = []
            self.waited_for_url: list[str] = []
            self.waited_for_timeout: list[int] = []

        async def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
            _ = timeout, wait_until
            self.url = url

        async def click(self, selector: str, *, timeout: int) -> None:
            _ = timeout
            self.clicked.append(selector)

        async def fill(self, selector: str, text: str, *, timeout: int) -> None:
            _ = timeout
            self.filled.append((selector, text))

        def locator(self, selector: str) -> "_FakeLocator":
            return _FakeLocator(self, selector)

        async def evaluate(self, script: str) -> object:
            if script == "window.scrollTo(0, document.body.scrollHeight)":
                self.scroll_to_bottom_calls += 1
                return None
            return {
                "title": "Example title",
                "body_text": "Hero section with pricing and CTA button",
                "headings": ["Hero", "Pricing"],
                "buttons": ["Start now", "Book demo"],
                "links": [
                    {"text": "Docs", "href": "https://example.com/docs"},
                    {"text": "Pricing", "href": "https://example.com/pricing"},
                ],
                "forms": [
                    {
                        "action": "/signup",
                        "method": "post",
                        "controls": [
                            {
                                "tag": "input",
                                "type": "email",
                                "name": "email",
                                "placeholder": "Email",
                                "label": "Email",
                            }
                        ],
                    }
                ],
                "images": [{"alt": "Hero screenshot", "src": "/hero.png"}],
            }

        async def content(self) -> str:
            return "<html>\n  <body>hello</body>\n</html>"

        async def screenshot(self, *, path: str, full_page: bool, timeout: int) -> None:
            _ = full_page, timeout
            Path(path).write_bytes(b"png")

        async def wait_for_selector(self, selector: str, *, timeout: int, state: str) -> None:
            _ = timeout, state
            self.waited_for_selector.append(selector)

        async def wait_for_function(self, expression: str, arg: str, *, timeout: int) -> None:
            _ = expression, timeout
            self.waited_for_text.append(arg)

        async def wait_for_url(self, url: str, *, timeout: int, wait_until: str) -> None:
            _ = timeout, wait_until
            self.waited_for_url.append(url)
            self.url = url

        async def wait_for_timeout(self, timeout: int) -> None:
            self.waited_for_timeout.append(timeout)

        async def title(self) -> str:
            return "Example title"

        async def close(self) -> None:
            return None

    class _FakeLocator:
        def __init__(self, page: _FakePage, selector: str) -> None:
            self._page = page
            self._selector = selector

        async def scroll_into_view_if_needed(self, *, timeout: int) -> None:
            _ = timeout
            self._page.scrolled_selectors.append(self._selector)

    class _FakeBrowser:
        def __init__(self) -> None:
            self.page = _FakePage()

        async def new_page(self) -> _FakePage:
            return self.page

        async def close(self) -> None:
            return None

    class _FakeChromium:
        async def launch(self, *, headless: bool) -> _FakeBrowser:
            launch_calls.append(headless)
            return _FakeBrowser()

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

        async def stop(self) -> None:
            return None

    async def _fake_start_playwright() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(tool, "_start_playwright", _fake_start_playwright)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    open_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "open",
            "url": "https://example.com",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    open_result = await tool.execute(ctx, open_params)
    assert open_result.ok is True
    assert open_result.payload["opened"] is True
    assert launch_calls == [False]

    navigate_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "navigate",
            "url": "https://example.com/next",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    navigate_result = await tool.execute(ctx, navigate_params)
    assert navigate_result.ok is True

    click_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "click",
            "selector": "#submit",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    click_result = await tool.execute(ctx, click_params)
    assert click_result.ok is True

    fill_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "fill",
            "selector": "#q",
            "text": "hello",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    fill_result = await tool.execute(ctx, fill_params)
    assert fill_result.ok is True
    assert fill_result.payload["chars"] == 5

    scroll_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "scroll",
            "selector": "footer",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    scroll_result = await tool.execute(ctx, scroll_params)
    assert scroll_result.ok is True
    assert scroll_result.payload["selector"] == "footer"

    bottom_scroll_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "scroll",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    bottom_scroll_result = await tool.execute(ctx, bottom_scroll_params)
    assert bottom_scroll_result.ok is True
    assert bottom_scroll_result.payload["position"] == "bottom"

    wait_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "wait",
            "selector": "#results",
            "timeout_ms": 2_000,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    wait_result = await tool.execute(ctx, wait_params)
    assert wait_result.ok is True
    assert wait_result.payload["wait_target"] == "selector"

    content_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "content",
            "max_chars": 15,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    content_result = await tool.execute(ctx, content_params)
    assert content_result.ok is True
    assert content_result.payload["truncated"] is True
    assert content_result.payload["title"] == "Example title"
    assert content_result.payload["content"] == "<html>\n  <body>"
    assert content_result.payload["text"] == "Hero section wi"
    assert content_result.payload["text_truncated"] is True
    active_session = await tool._sessions.get(
        root_dir=settings.root_dir,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
        idle_ttl_sec=settings.browser_session_idle_ttl_sec,
    )
    assert active_session is not None
    assert active_session.page.scrolled_selectors == ["footer"]
    assert active_session.page.scroll_to_bottom_calls == 1
    assert active_session.page.waited_for_selector == ["#results"]

    snapshot_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "snapshot",
            "path": "tmp/review.png",
            "max_chars": 24,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    snapshot_result = await tool.execute(ctx, snapshot_params)
    assert snapshot_result.ok is True
    assert snapshot_result.payload["title"] == "Example title"
    assert snapshot_result.payload["snapshot"]["headings"] == ["Hero", "Pricing"]
    assert snapshot_result.payload["artifact"]["kind"] == "browser_snapshot"
    assert Path(tmp_path / "tmp" / "review.png").exists()
    assert Path(tmp_path / "tmp" / "review.txt").exists()
    assert Path(tmp_path / "tmp" / "review.html").exists()
    assert Path(tmp_path / "tmp" / "review.json").exists()
    assert (tmp_path / "tmp" / "review.html").read_text(encoding="utf-8").startswith("<html>\n  <body>")
    snapshot_json = json.loads((tmp_path / "tmp" / "review.json").read_text(encoding="utf-8"))
    assert snapshot_json["title"] == "Example title"
    assert snapshot_json["links"][0]["href"] == "https://example.com/docs"

    snapshot_text_path_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "snapshot",
            "path": "tmp/report.txt",
            "max_chars": 24,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    snapshot_text_path_result = await tool.execute(ctx, snapshot_text_path_params)
    assert snapshot_text_path_result.ok is True
    assert Path(tmp_path / "tmp" / "report.png").exists()
    report_text = Path(tmp_path / "tmp" / "report.txt").read_text(encoding="utf-8")
    assert report_text.startswith("Hero section with")
    assert len(report_text) == 24
    assert Path(tmp_path / "tmp" / "report.html").exists()
    assert Path(tmp_path / "tmp" / "report.json").exists()

    screenshot_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "screenshot",
            "path": "tmp/shot.png",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    screenshot_result = await tool.execute(ctx, screenshot_params)
    assert screenshot_result.ok is True
    assert Path(tmp_path / "tmp" / "shot.png").exists()

    close_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "close",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    close_result = await tool.execute(ctx, close_params)
    assert close_result.ok is True
    assert close_result.payload["closed"] is True


async def test_browser_control_supports_semantic_targets_and_extended_actions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """browser.control should support semantic element targeting and extended input actions."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None

    class _FakeKeyboard:
        def __init__(self) -> None:
            self.pressed: list[str] = []

        async def press(self, key: str) -> None:
            self.pressed.append(key)

    class _FakeLocator:
        def __init__(self, page: "_SemanticPage", target: str) -> None:
            self._page = page
            self._target = target

        async def click(self, *, timeout: int) -> None:
            self._page.click_calls.append((self._target, timeout))

        async def fill(self, text: str, *, timeout: int) -> None:
            self._page.fill_calls.append((self._target, text, timeout))

        async def press(self, key: str, *, timeout: int) -> None:
            self._page.locator_press_calls.append((self._target, key, timeout))

        async def select_option(self, **kwargs: object) -> None:
            self._page.select_calls.append((self._target, kwargs))

        async def check(self, *, timeout: int) -> None:
            self._page.check_calls.append((self._target, timeout))

        async def wait_for(self, *, timeout: int, state: str) -> None:
            self._page.wait_calls.append((self._target, timeout, state))

        async def scroll_into_view_if_needed(self, *, timeout: int) -> None:
            self._page.scroll_calls.append((self._target, timeout))

    class _SemanticPage:
        def __init__(self) -> None:
            self.url = "https://example.com/login"
            self.keyboard = _FakeKeyboard()
            self.click_calls: list[tuple[str, int]] = []
            self.fill_calls: list[tuple[str, str, int]] = []
            self.locator_press_calls: list[tuple[str, str, int]] = []
            self.select_calls: list[tuple[str, dict[str, object]]] = []
            self.check_calls: list[tuple[str, int]] = []
            self.wait_calls: list[tuple[str, int, str]] = []
            self.scroll_calls: list[tuple[str, int]] = []

        async def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
            _ = timeout, wait_until
            self.url = url

        def locator(self, selector: str) -> _FakeLocator:
            return _FakeLocator(self, f"selector:{selector}")

        def get_by_label(self, label: str, *, exact: bool) -> _FakeLocator:
            return _FakeLocator(self, f"label:{label}:exact={exact}")

        def get_by_placeholder(self, placeholder: str, *, exact: bool) -> _FakeLocator:
            return _FakeLocator(self, f"placeholder:{placeholder}:exact={exact}")

        def get_by_role(self, role: str, **kwargs: object) -> _FakeLocator:
            name = str(kwargs.get("name") or "")
            exact = bool(kwargs.get("exact"))
            return _FakeLocator(self, f"role:{role}:name={name}:exact={exact}")

        def get_by_text(self, text: str, *, exact: bool) -> _FakeLocator:
            return _FakeLocator(self, f"text:{text}:exact={exact}")

        async def evaluate(self, script: str) -> object:
            _ = script
            return {
                "title": "Checkout",
                "body_text": "Checkout page",
                "headings": ["Checkout"],
                "buttons": ["Continue"],
                "links": [],
                "forms": [],
                "images": [],
                "interactives": [
                    {
                        "tag": "input",
                        "role": "textbox",
                        "type": "email",
                        "text": "",
                        "name": "email",
                        "label": "Email",
                        "placeholder": "Email address",
                        "href": "",
                    },
                    {
                        "tag": "button",
                        "role": "button",
                        "type": "",
                        "text": "Continue",
                        "name": "",
                        "label": "",
                        "placeholder": "",
                        "href": "",
                    },
                ],
            }

        async def content(self) -> str:
            return "<html><body>checkout</body></html>"

        async def screenshot(self, *, path: str, full_page: bool, timeout: int) -> None:
            _ = full_page, timeout
            Path(path).write_bytes(b"png")

        async def title(self) -> str:
            return "Checkout"

        async def close(self) -> None:
            return None

    class _FakeBrowser:
        def __init__(self) -> None:
            self.page = _SemanticPage()

        async def new_page(self) -> _SemanticPage:
            return self.page

        async def close(self) -> None:
            return None

    class _FakeChromium:
        async def launch(self, *, headless: bool) -> _FakeBrowser:
            _ = headless
            return _FakeBrowser()

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

        async def stop(self) -> None:
            return None

    async def _fake_start_playwright() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(tool, "_start_playwright", _fake_start_playwright)
    ctx = ToolContext(profile_id="default", session_id="s-semantic", run_id=1)

    open_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "open",
                "url": "https://example.com/login",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert open_result.ok is True

    fill_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "fill",
                "label": "Email",
                "text": "user@example.com",
                "exact": True,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert fill_result.ok is True

    click_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "click",
                "role": "button",
                "target_text": "Continue",
                "exact": True,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert click_result.ok is True

    press_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "press",
                "key": "Enter",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert press_result.ok is True

    select_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "select",
                "field_name": "country",
                "target_text": "Germany",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert select_result.ok is True

    check_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "check",
                "label": "Accept terms",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert check_result.ok is True

    wait_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "wait",
                "role": "button",
                "target_text": "Continue",
                "state": "visible",
                "timeout_ms": 12_000,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert wait_result.ok is True
    assert wait_result.payload["wait_target"] == "target"

    snapshot_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "snapshot",
                "path": "tmp/semantic-checkout.png",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert snapshot_result.ok is True
    assert snapshot_result.payload["snapshot"]["interactives"][0]["label"] == "Email"

    active_session = await tool._sessions.get(  # noqa: SLF001
        root_dir=settings.root_dir,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
        idle_ttl_sec=settings.browser_session_idle_ttl_sec,
    )
    assert active_session is not None
    page = active_session.page
    assert page.fill_calls == [("label:Email:exact=True", "user@example.com", 15000)]
    assert page.click_calls == [("role:button:name=Continue:exact=True", 15000)]
    assert page.keyboard.pressed == ["Enter"]
    assert page.select_calls == [
        ("selector:[name=\"country\"]", {"label": "Germany", "timeout": 15000})
    ]
    assert page.check_calls == [("label:Accept terms:exact=False", 15000)]
    assert page.wait_calls == [("role:button:name=Continue:exact=False", 12000, "visible")]

    close_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "close",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert close_result.ok is True


async def test_browser_control_aligns_tool_timeout_with_browser_timeout(tmp_path: Path) -> None:
    """browser.control should derive tool timeout from timeout_ms and clip mismatched explicit values."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None

    derived = tool.parse_params(
        {
            "profile_key": "default",
            "action": "wait",
            "timeout_ms": 45_000,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    explicit = tool.parse_params(
        {
            "profile_key": "default",
            "action": "wait",
            "timeout_ms": 45_000,
            "timeout_sec": 5,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    short = tool.parse_params(
        {
            "profile_key": "default",
            "action": "wait",
            "timeout_ms": 5_000,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    assert derived.timeout_sec == 45
    assert derived.timeout_ms == 45_000
    assert explicit.timeout_sec == 5
    assert explicit.timeout_ms == 5_000
    assert short.timeout_sec == 5
    assert short.timeout_ms == 5_000


async def test_browser_control_persists_state_only_for_stateful_actions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """browser.control should skip storage-state writes for read-only browser actions."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None
    ctx = ToolContext(profile_id="default", session_id="s-browser-persist", run_id=1)

    persisted_actions: list[str] = []

    async def _fake_execute_action(*, ctx: ToolContext, payload):  # type: ignore[no-untyped-def]
        _ = ctx
        return {"action": payload.action}

    async def _fake_persist_session_state(*, root_dir: Path, profile_id: str, session_id: str) -> bool:
        _ = root_dir, profile_id, session_id
        persisted_actions.append(current_action[0])
        return True

    monkeypatch.setattr(tool, "_execute_action", _fake_execute_action)
    monkeypatch.setattr(tool._sessions, "persist_session_state", _fake_persist_session_state)

    current_action = ["content"]
    content_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": current_action[0],
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    current_action[0] = "click"
    click_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": current_action[0],
                "selector": "#submit",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert content_result.ok is True
    assert click_result.ok is True
    assert persisted_actions == ["click"]


async def test_browser_control_returns_graceful_errors(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """browser.control should fail gracefully when unavailable or action is invalid."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    navigate_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "navigate",
            "url": "https://example.com",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    navigate_result = await tool.execute(ctx, navigate_params)
    assert navigate_result.ok is False
    assert navigate_result.error_code == "browser_session_not_open"
    assert navigate_result.metadata["browser_error_class"] == "browser_session_missing"
    assert navigate_result.metadata["suggested_next_action"] == "open_session"

    click_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "click",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    click_result = await tool.execute(ctx, click_params)
    assert click_result.ok is False
    assert click_result.error_code == "browser_invalid"

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.browser_control.plugin._PLAYWRIGHT_FACTORY",
        None,
    )
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.browser_control.plugin._PLAYWRIGHT_IMPORT_ERROR",
        RuntimeError("missing"),
    )
    open_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "open",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    open_result = await tool.execute(ctx, open_params)
    assert open_result.ok is False
    assert open_result.error_code == "browser_unavailable"
    assert "afk browser install" in str(open_result.reason or "")
    assert open_result.metadata["browser_error_class"] == "browser_runtime_missing"


async def test_browser_control_lightpanda_missing_cdp_url_points_to_browser_install(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Lightpanda browser startup errors should direct operators to the browser install flow."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        browser_backend="lightpanda_cdp",
        browser_cdp_url=None,
    )
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None
    ctx = ToolContext(profile_id="default", session_id="s-lightpanda", run_id=1)

    class _FakeChromium:
        async def connect_over_cdp(self, cdp_url: str) -> object:  # pragma: no cover - should not be called
            _ = cdp_url
            raise AssertionError("connect_over_cdp must not run when the CDP URL is missing")

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

        async def start(self) -> "_FakePlaywright":
            return self

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.browser_control.plugin._PLAYWRIGHT_FACTORY",
        lambda: _FakePlaywright(),
    )
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.browser_control.plugin._PLAYWRIGHT_IMPORT_ERROR",
        None,
    )
    params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "open",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert result.ok is False
    assert result.error_code == "browser_unavailable"
    assert "afk browser install" in str(result.reason or "")
    assert "browser cdp-url" in str(result.reason or "")


async def test_browser_control_marks_target_closed_as_resettable_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Target-closed browser errors should invalidate the session and expose retry metadata."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None
    ctx = ToolContext(profile_id="default", session_id="s-dead", run_id=1)

    class _FakePage:
        url = "about:blank"

        async def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
            _ = url, timeout, wait_until
            raise RuntimeError("TargetClosedError: Page.goto: Target page, context or browser has been closed")

        def is_closed(self) -> bool:
            return False

        async def close(self) -> None:
            return None

    class _FakeBrowser:
        def __init__(self) -> None:
            self.page = _FakePage()
            self.closed = False

        async def new_page(self) -> _FakePage:
            return self.page

        def is_connected(self) -> bool:
            return not self.closed

        async def close(self) -> None:
            self.closed = True

    class _FakeChromium:
        async def launch(self, *, headless: bool) -> _FakeBrowser:
            _ = headless
            return _FakeBrowser()

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    async def _fake_start_playwright() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(tool, "_start_playwright", _fake_start_playwright)
    open_params = tool.parse_params(
        {
            "profile_key": "default",
            "action": "open",
            "url": "https://example.com",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    open_result = await tool.execute(ctx, open_params)

    assert open_result.ok is False
    assert open_result.error_code == "browser_action_failed"
    assert open_result.metadata["browser_error_class"] == "browser_target_closed"
    assert open_result.metadata["requires_session_reset"] is True
    manager = get_browser_session_manager()
    assert await manager.get(
        root_dir=tmp_path,
        profile_id="default",
        session_id="s-dead",
        idle_ttl_sec=settings.browser_session_idle_ttl_sec,
    ) is None


async def test_browser_control_reuses_existing_session_across_tool_instances(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """browser.control should reuse live browser session across separate registry instances."""

    settings = Settings(root_dir=tmp_path)
    first_registry = _registry(settings)
    second_registry = _registry(settings)
    first_tool = first_registry.get("browser.control")
    second_tool = second_registry.get("browser.control")
    assert first_tool is not None
    assert second_tool is not None
    launches: list[int] = []

    class _FakePage:
        def __init__(self) -> None:
            self.url = "about:blank"

        async def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
            _ = timeout, wait_until
            self.url = url

        async def content(self) -> str:
            return "<html><body>reused</body></html>"

        async def close(self) -> None:
            return None

    class _FakeBrowser:
        def __init__(self, page: _FakePage) -> None:
            self.page = page

        async def new_page(self) -> _FakePage:
            return self.page

        async def close(self) -> None:
            return None

    class _FakeChromium:
        async def launch(self, *, headless: bool) -> _FakeBrowser:
            _ = headless
            launches.append(1)
            return _FakeBrowser(shared_page)

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

        async def stop(self) -> None:
            return None

    shared_page = _FakePage()

    async def _fake_start_playwright() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(first_tool, "_start_playwright", _fake_start_playwright)
    monkeypatch.setattr(second_tool, "_start_playwright", _fake_start_playwright)
    ctx = ToolContext(profile_id="default", session_id="s-reuse", run_id=1)

    open_params = first_tool.parse_params(
        {
            "profile_key": "default",
            "action": "open",
            "url": "https://example.com",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    content_params = second_tool.parse_params(
        {
            "profile_key": "default",
            "action": "content",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    close_params = second_tool.parse_params(
        {
            "profile_key": "default",
            "action": "close",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    open_result = await first_tool.execute(ctx, open_params)
    content_result = await second_tool.execute(ctx, content_params)
    close_result = await second_tool.execute(ctx, close_params)

    assert open_result.ok is True
    assert open_result.payload["reused"] is False
    assert content_result.ok is True
    assert content_result.payload["content"] == "<html><body>reused</body></html>"
    assert close_result.ok is True
    assert close_result.payload["closed"] is True
    assert len(launches) == 1


async def test_browser_control_persists_storage_state_across_reopen_and_can_clear_it(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """browser.control should reload persisted auth state on reopen and support clearing it."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("browser.control")
    assert tool is not None
    launches: list[dict[str, object]] = []

    class _FakePage:
        def __init__(self) -> None:
            self.url = "about:blank"

        async def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
            _ = timeout, wait_until
            self.url = url

        async def content(self) -> str:
            return "<html><body>persisted</body></html>"

        async def evaluate(self, script: str) -> object:
            _ = script
            return {
                "title": "Persisted session",
                "body_text": "Logged in",
                "headings": ["Dashboard"],
                "buttons": ["Log out"],
                "links": [],
                "forms": [],
                "images": [],
                "interactives": [],
            }

        async def title(self) -> str:
            return "Persisted session"

        async def close(self) -> None:
            return None

    class _FakeContext:
        def __init__(self, page: _FakePage) -> None:
            self._page = page
            self.saved_paths: list[str] = []

        async def new_page(self) -> _FakePage:
            return self._page

        async def storage_state(self, *, path: str) -> None:
            self.saved_paths.append(path)
            Path(path).write_text('{"cookies":[{"name":"sid"}],"origins":[]}', encoding="utf-8")

        async def close(self) -> None:
            return None

    class _FakeBrowser:
        def __init__(self, launch_record: dict[str, object]) -> None:
            self._page = _FakePage()
            self._launch_record = launch_record

        async def new_context(self, *, storage_state: str | None = None) -> _FakeContext:
            self._launch_record["storage_state"] = storage_state
            return _FakeContext(self._page)

        async def close(self) -> None:
            return None

    class _FakeChromium:
        async def launch(self, *, headless: bool) -> _FakeBrowser:
            record = {"headless": headless, "storage_state": None}
            launches.append(record)
            return _FakeBrowser(record)

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

        async def stop(self) -> None:
            return None

    async def _fake_start_playwright() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(tool, "_start_playwright", _fake_start_playwright)
    ctx = ToolContext(profile_id="default", session_id="s-persisted", run_id=1)
    state_path = tool._sessions.storage_state_path(  # noqa: SLF001
        root_dir=settings.root_dir,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )

    open_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "open",
                "url": "https://example.com/account",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert open_result.ok is True
    assert open_result.payload["storage_state_loaded"] is False
    assert state_path.exists()

    close_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "close",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert close_result.ok is True
    assert state_path.exists()

    reopen_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "open",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert reopen_result.ok is True
    assert reopen_result.payload["storage_state_loaded"] is True
    assert launches[-1]["storage_state"] == str(state_path)

    reset_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "action": "close",
                "clear_state": True,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert reset_result.ok is True
    assert reset_result.payload["clear_state"] is True
    assert not state_path.exists()
