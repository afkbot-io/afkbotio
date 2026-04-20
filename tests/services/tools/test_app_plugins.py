"""Integration tests for app-oriented tool plugins."""

from __future__ import annotations

import io
import json
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Literal, cast
from urllib.error import HTTPError
from urllib.request import Request

from cryptography.fernet import Fernet
from pytest import MonkeyPatch
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
from afkbot.services.apps.imap.actions import _search_messages_sync
from afkbot.services.apps.registry import get_app_registry
from afkbot.services.apps.smtp.actions import _send_email_sync
from afkbot.services.apps.telegram.http_api import _resolve_workspace_media_path
from afkbot.services.credentials import reset_credentials_services_async
from afkbot.services.tools.base import ToolCall, ToolContext
from afkbot.services.tools.network.pinned_opener import _NoRedirect
from afkbot.services.tools.plugins.http_request.plugin import HttpRequestTool
from afkbot.services.tools.params import ToolParametersValidationError
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


async def _noop_async(*_: object, **__: object) -> None:
    """Test helper for async callbacks that intentionally do nothing."""


async def _prepare(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> tuple[Settings, AsyncEngine, ToolRegistry]:
    key = Fernet.generate_key().decode("utf-8")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tools_apps.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", key)
    get_settings.cache_clear()
    await reset_credentials_services_async()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
    return settings, engine, ToolRegistry.from_settings(settings)


async def _set_network_allowlist(
    *,
    settings: Settings,
    profile_id: str,
    hosts: list[str],
) -> None:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            policy = await ProfilePolicyRepository(session).get_or_create_default(profile_id)
            policy.network_allowlist_json = json.dumps(hosts, ensure_ascii=True)
            await session.flush()
    finally:
        await engine.dispose()


async def _create_credential(
    *,
    registry: ToolRegistry,
    settings: Settings,
    ctx: ToolContext,
    app_name: str,
    profile_name: str,
    credential_slug: str,
    value: str,
) -> None:
    create_tool = registry.get("credentials.create")
    assert create_tool is not None
    params = create_tool.parse_params(
        {
            "profile_key": ctx.profile_id,
            "app_name": app_name,
            "profile_name": profile_name,
            "credential_slug": credential_slug,
            "value": value,
            "replace_existing": True,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await create_tool.execute(ctx, params)
    assert result.ok is True


async def test_http_request_tool_success(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """http.request should return normalized payload from request implementation."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        tool = registry.get("http.request")
        assert tool is not None

        async def _fake_request(*, payload: object, headers: object) -> dict[str, object]:
            assert payload is not None
            assert isinstance(headers, dict)
            return {
                "method": "GET",
                "url": "https://example.com",
                "status_code": 200,
                "headers": {"content-type": "application/json"},
                "body": '{"ok":true}',
                "json": {"ok": True},
            }

        monkeypatch.setattr(tool, "_perform_request", _fake_request)
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "https://example.com",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is True
        assert result.payload["status_code"] == 200
    finally:
        await engine.dispose()


async def test_http_request_uses_credential_profile_header(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """http.request should resolve auth header value from credentials when requested."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="http",
            profile_name="default",
            credential_slug="http_auth",
            value="Bearer token-123",
        )

        tool = registry.get("http.request")
        assert tool is not None

        captured: dict[str, str] = {}

        async def _fake_request(*, payload: object, headers: object) -> dict[str, object]:
            assert payload is not None
            assert isinstance(headers, dict)
            captured.update({str(k): str(v) for k, v in headers.items()})
            return {
                "method": "GET",
                "url": "https://example.com",
                "status_code": 200,
                "headers": {"content-type": "application/json"},
                "body": '{"ok":true}',
                "json": {"ok": True},
            }

        monkeypatch.setattr(tool, "_perform_request", _fake_request)
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "https://example.com",
                "auth_credential_name": "http_auth",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(ctx, params)
        assert result.ok is True
        assert captured["Authorization"] == "Bearer token-123"
    finally:
        await engine.dispose()


async def test_http_request_missing_auth_credential_returns_secure_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """http.request should surface credentials_missing metadata for secure flow."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        tool = registry.get("http.request")
        assert tool is not None
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "https://example.com",
                "auth_credential_name": "http_auth_missing",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is False
        assert result.error_code == "credentials_missing"
        assert result.metadata["integration_name"] == "http"
        assert result.metadata["credential_name"] == "http_auth_missing"
    finally:
        await engine.dispose()


async def test_http_request_redacts_secret_from_error_reason(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """http.request should redact resolved secret values from raised error messages."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="http",
            profile_name="default",
            credential_slug="http_auth",
            value="Bearer ultra-secret-token",
        )

        tool = registry.get("http.request")
        assert tool is not None

        async def _fake_request(*, payload: object, headers: object) -> dict[str, object]:
            _ = payload, headers
            raise RuntimeError("upstream rejected header Bearer ultra-secret-token")

        monkeypatch.setattr(tool, "_perform_request", _fake_request)
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "https://example.com",
                "auth_credential_name": "http_auth",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(ctx, params)
        assert result.ok is False
        assert result.error_code == "http_request_failed"
        reason = str(result.reason or "")
        assert "ultra-secret-token" not in reason
        assert "[REDACTED]" in reason
    finally:
        await engine.dispose()


async def test_http_request_rejects_non_http_scheme(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """http.request should reject file:// and other non-http schemes."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        tool = registry.get("http.request")
        assert tool is not None
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "file:///etc/hosts",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is False
        assert result.error_code == "http_request_invalid"
    finally:
        await engine.dispose()


async def test_http_request_rejects_non_public_target_resolution(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """http.request should block URLs resolved to non-public IP ranges."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        tool = registry.get("http.request")
        assert tool is not None
        monkeypatch.setattr(
            "afkbot.services.tools.plugins.http_request.plugin.HttpRequestTool._resolve_host_addresses",
            staticmethod(lambda *, host, port: ("127.0.0.1",)),
        )
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "https://example.com",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is False
        assert result.error_code == "http_request_invalid"
        assert "non-public network address" in str(result.reason)
    finally:
        await engine.dispose()


async def test_http_request_rejects_localhost_target(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """http.request should reject localhost targets deterministically."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        tool = registry.get("http.request")
        assert tool is not None
        params = tool.parse_params(
            {
                "profile_key": "default",
                "method": "GET",
                "url": "https://localhost/api",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is False
        assert result.error_code == "http_request_invalid"
        assert "must not target localhost" in str(result.reason or "")
    finally:
        await engine.dispose()


def test_http_no_redirect_handler_returns_none() -> None:
    """No-redirect handler should block redirect follow-up."""

    handler = _NoRedirect()
    req = Request("https://example.com")
    redirected = handler.redirect_request(
        req, fp=object(), code=302, msg="Found", headers={}, newurl="https://127.0.0.1/"
    )
    assert redirected is None


def test_http_request_sync_limits_error_body(monkeypatch: MonkeyPatch) -> None:
    """http.request sync path should truncate oversized HTTP error body."""

    class _FakeOpener:
        def open(self, request: Request, timeout: float) -> object:  # noqa: ARG002
            raise HTTPError(
                request.full_url,
                500,
                "server error",
                hdrs={},
                fp=io.BytesIO(b"x" * 64),
            )

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.http_request.plugin.build_pinned_opener",
        lambda *args, **kwargs: _FakeOpener(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        HttpRequestTool._perform_request_sync(
            method="GET",
            url="https://example.com",
            headers={},
            body=None,
            timeout_sec=5,
            max_body_bytes=8,
        )
    message = str(exc_info.value)
    assert "HTTP 500:" in message
    assert "[truncated]" in message


def test_app_registry_defines_builtin_skills_and_canonical_actions() -> None:
    """Builtin app registry should expose one canonical action contract per app."""

    registry = get_app_registry()

    telegram = registry.get("telegram")
    assert telegram is not None
    assert telegram.allowed_skills == {"telegram"}
    assert telegram.allowed_actions == {
        "send_document",
        "send_message",
        "send_photo",
        "send_chat_action",
        "get_me",
        "get_updates",
        "ban_chat_member",
        "unban_chat_member",
    }

    partyflow = registry.get("partyflow")
    assert partyflow is not None
    assert partyflow.allowed_skills == {"partyflow"}
    assert partyflow.allowed_actions == {
        "get_me",
        "join_conversation",
        "send_message",
    }

    smtp = registry.get("smtp")
    assert smtp is not None
    assert smtp.allowed_skills == {"smtp"}
    assert smtp.allowed_actions == {"send_email"}

    imap = registry.get("imap")
    assert imap is not None
    assert imap.allowed_skills == {"imap"}
    assert imap.allowed_actions == {"search_messages"}


async def test_app_list_includes_profile_apps(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """app.list should include builtin and profile-local app registrations."""

    monkeypatch.setenv("AFKBOT_ENABLE_PROFILE_APP_MODULES", "1")
    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        profile_app_path = tmp_path / "profiles/default/apps/ping/APP.py"
        profile_app_path.parent.mkdir(parents=True, exist_ok=True)
        profile_app_path.write_text(
            "\n".join(
                (
                    "from afkbot.services.tools.base import ToolResult",
                    "",
                    "def register_apps(register_app):",
                    "    @register_app(",
                    '        name="ping",',
                    '        allowed_skills={"ping-skill"},',
                    '        allowed_actions={"echo"},',
                    "    )",
                    "    async def _run(settings, ctx, action, params):",
                    "        _ = settings, ctx",
                    "        return ToolResult(ok=True, payload={",
                    '            "action": action,',
                    '            "echo": str(params.get("text") or ""),',
                    "        })",
                )
            ),
            encoding="utf-8",
        )

        app_list = registry.get("app.list")
        assert app_list is not None
        params = app_list.parse_params(
            {
                "profile_key": "default",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_list.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is True

        items = cast(list[dict[str, object]], result.payload["apps"])
        names = {str(item.get("name")) for item in items}
        assert {"telegram", "smtp", "imap", "ping"}.issubset(names)
        telegram_item = next(item for item in items if item.get("name") == "telegram")
        telegram_schemas = cast(dict[str, object], telegram_item["action_schemas"])
        assert "send_photo" in telegram_schemas
        assert "send_document" in telegram_schemas
        send_message_schema = cast(dict[str, object], telegram_schemas["send_message"])
        send_fields = cast(list[dict[str, object]], send_message_schema["fields"])
        field_map = {str(item["name"]): item for item in send_fields}
        assert send_message_schema["action"] == "send_message"
        assert field_map["text"]["required"] is True
        assert field_map["chat_id"]["required"] is False
        assert field_map["disable_web_page_preview"]["type"] == "boolean"
        ping_item = next(item for item in items if item.get("name") == "ping")
        assert ping_item["allowed_skills"] == ["ping-skill"]
        assert ping_item["allowed_actions"] == ["echo"]
        assert "action_schemas" not in ping_item
        assert ping_item["source"] == "profile"
        assert str(ping_item["source_path"]).endswith("profiles/default/apps/ping/APP.py")
    finally:
        await engine.dispose()


async def test_app_list_skips_profile_apps_when_runtime_loading_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.list should not load profile APP.py modules unless explicitly enabled."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        profile_app_path = tmp_path / "profiles/default/apps/ping/APP.py"
        profile_app_path.parent.mkdir(parents=True, exist_ok=True)
        profile_app_path.write_text(
            "\n".join(
                (
                    "from afkbot.services.tools.base import ToolResult",
                    "",
                    "def register_apps(register_app):",
                    "    @register_app(",
                    '        name="ping",',
                    '        allowed_skills={"ping-skill"},',
                    '        allowed_actions={"echo"},',
                    "    )",
                    "    async def _run(settings, ctx, action, params):",
                    "        _ = settings, ctx",
                    "        return ToolResult(ok=True, payload={",
                    '            "action": action,',
                    '            "echo": str(params.get("text") or ""),',
                    "        })",
                )
            ),
            encoding="utf-8",
        )

        app_list = registry.get("app.list")
        assert app_list is not None
        params = app_list.parse_params(
            {
                "profile_key": "default",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_list.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is True

        items = cast(list[dict[str, object]], result.payload["apps"])
        names = {str(item.get("name")) for item in items}
        assert "ping" not in names
    finally:
        await engine.dispose()


async def test_app_run_uses_profile_local_registry(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """app.run should execute profile-local app registered under profiles/<id>/apps."""

    monkeypatch.setenv("AFKBOT_ENABLE_PROFILE_APP_MODULES", "1")
    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        profile_app_path = tmp_path / "profiles/default/apps/ping/APP.py"
        profile_app_path.parent.mkdir(parents=True, exist_ok=True)
        profile_app_path.write_text(
            "\n".join(
                (
                    "from afkbot.services.tools.base import ToolResult",
                    "",
                    "def register_apps(register_app):",
                    "    @register_app(",
                    '        name="ping",',
                    '        allowed_skills={"ping-skill"},',
                    '        allowed_actions={"echo"},',
                    "    )",
                    "    async def _run(settings, ctx, action, params):",
                    "        _ = settings, ctx",
                    "        return ToolResult(ok=True, payload={",
                    '            "action": action,',
                    '            "echo": str(params.get("text") or ""),',
                    "        })",
                )
            ),
            encoding="utf-8",
        )

        app_tool = registry.get("app.run")
        assert app_tool is not None
        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "ping",
                "action": "echo",
                "profile_name": "default",
                "params": {"text": "hello"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )
        assert result.ok is True
        assert result.payload["action"] == "echo"
        assert result.payload["echo"] == "hello"
    finally:
        await engine.dispose()


async def test_app_run_telegram_smtp_imap_with_credentials(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should execute telegram/smtp/imap actions with stored credentials."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org", "smtp.example.com", "imap.example.com"],
        )
        app_tool = registry.get("app.run")
        assert app_tool is not None

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )

        send_calls: list[dict[str, object]] = []

        async def _fake_post_send_message(**kwargs: object) -> dict[str, object]:
            send_calls.append(dict(kwargs))
            return {"ok": True, "action": "send_message", "message_id": 777, "chat_id": "1001"}

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        tg_params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {"text": "hello"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        tg_result = await app_tool.execute(ctx, tg_params)
        assert tg_result.ok is True
        assert tg_result.payload["message_id"] == 777
        assert send_calls[0]["message_thread_id"] is None

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="smtp",
            profile_name="default",
            credential_slug="smtp_host",
            value="smtp.example.com",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="smtp",
            profile_name="default",
            credential_slug="smtp_port",
            value="587",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="smtp",
            profile_name="default",
            credential_slug="smtp_username",
            value="user@example.com",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="smtp",
            profile_name="default",
            credential_slug="smtp_password",
            value="secret-password",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="smtp",
            profile_name="default",
            credential_slug="smtp_from_email",
            value="bot@example.com",
        )

        async def _fake_send_email(**_: object) -> None:
            return None

        monkeypatch.setattr(
            "afkbot.services.apps.smtp.actions._send_email",
            _fake_send_email,
        )

        smtp_params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "smtp",
                "action": "send_email",
                "profile_name": "default",
                "params": {
                    "to_email": "to@example.com",
                    "subject": "subject",
                    "body": "body",
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        smtp_result = await app_tool.execute(ctx, smtp_params)
        assert smtp_result.ok is True

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="imap",
            profile_name="default",
            credential_slug="imap_host",
            value="imap.example.com",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="imap",
            profile_name="default",
            credential_slug="imap_port",
            value="993",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="imap",
            profile_name="default",
            credential_slug="imap_username",
            value="user@example.com",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="imap",
            profile_name="default",
            credential_slug="imap_password",
            value="secret-imap-password",
        )

        async def _fake_search(**_: object) -> list[dict[str, object]]:
            return [
                {
                    "id": "1",
                    "subject": "Hello",
                    "from": "sender@example.com",
                    "date": "Mon, 1 Jan 2026 12:00:00 +0000",
                }
            ]

        monkeypatch.setattr(
            "afkbot.services.apps.imap.actions._search_messages",
            _fake_search,
        )

        imap_params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "imap",
                "action": "search_messages",
                "profile_name": "default",
                "params": {"query": "ALL", "limit": 10},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        imap_result = await app_tool.execute(ctx, imap_params)
        assert imap_result.ok is True
        assert imap_result.payload["count"] == 1
    finally:
        await engine.dispose()


async def test_app_run_telegram_auto_picks_single_profile_when_omitted(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should auto-pick the only available credential profile when profile_name is omitted."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        app_tool = registry.get("app.run")
        assert app_tool is not None

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="work",
            credential_slug="telegram_token",
            value="auto-pick-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="work",
            credential_slug="telegram_chat_id",
            value="2001",
        )

        async def _fake_post_send_message(**_: object) -> dict[str, object]:
            return {"ok": True, "action": "send_message", "message_id": 778, "chat_id": "2001"}

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "params": {"text": "hello"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)
        assert result.ok is True
        assert result.payload["message_id"] == 778
    finally:
        await engine.dispose()


async def test_app_run_smtp_redacts_malformed_secret_backed_port(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """SMTP port parse failures must not echo secret credential values."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None

        for slug, value in (
            ("smtp_host", "smtp.example.com"),
            ("smtp_port", "super-secret-not-a-port"),
            ("smtp_username", "user@example.com"),
            ("smtp_password", "secret-password"),
            ("smtp_from_email", "bot@example.com"),
        ):
            await _create_credential(
                registry=registry,
                settings=settings,
                ctx=ctx,
                app_name="smtp",
                profile_name="default",
                credential_slug=slug,
                value=value,
            )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "smtp",
                "action": "send_email",
                "profile_name": "default",
                "params": {"to_email": "to@example.com", "subject": "subject", "body": "body"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert result.reason == "SMTP port must be an integer"
        assert "super-secret-not-a-port" not in str(result.reason or "")
    finally:
        await engine.dispose()


async def test_app_run_imap_redacts_malformed_secret_backed_port(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """IMAP port parse failures must not echo secret credential values."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None

        for slug, value in (
            ("imap_host", "imap.example.com"),
            ("imap_port", "super-secret-not-a-port"),
            ("imap_username", "user@example.com"),
            ("imap_password", "secret-password"),
        ):
            await _create_credential(
                registry=registry,
                settings=settings,
                ctx=ctx,
                app_name="imap",
                profile_name="default",
                credential_slug=slug,
                value=value,
            )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "imap",
                "action": "search_messages",
                "profile_name": "default",
                "params": {"query": "ALL", "limit": 5},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert result.reason == "IMAP port must be an integer"
        assert "super-secret-not-a-port" not in str(result.reason or "")
    finally:
        await engine.dispose()


async def test_app_run_enforces_network_allowlist(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should fail when integration hosts are not allowed by profile policy."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["allowed.example.com"],
        )

        for slug, value in (
            ("telegram_token", "token"),
            ("telegram_chat_id", "1001"),
        ):
            await _create_credential(
                registry=registry,
                settings=settings,
                ctx=ctx,
                app_name="telegram",
                profile_name="default",
                credential_slug=slug,
                value=value,
            )

        for app_name, slug, value in (
            ("smtp", "smtp_host", "blocked.example.com"),
            ("smtp", "smtp_port", "587"),
            ("smtp", "smtp_username", "u"),
            ("smtp", "smtp_password", "p"),
            ("smtp", "smtp_from_email", "bot@example.com"),
            ("imap", "imap_host", "blocked.example.com"),
            ("imap", "imap_port", "993"),
            ("imap", "imap_username", "u"),
            ("imap", "imap_password", "p"),
        ):
            await _create_credential(
                registry=registry,
                settings=settings,
                ctx=ctx,
                app_name=app_name,
                profile_name="default",
                credential_slug=slug,
                value=value,
            )

        app_tool = registry.get("app.run")
        assert app_tool is not None

        tg_params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {"text": "hello"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        tg_result = await app_tool.execute(ctx, tg_params)
        assert tg_result.ok is False
        assert tg_result.error_code == "profile_policy_violation"

        smtp_params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "smtp",
                "action": "send_email",
                "profile_name": "default",
                "params": {
                    "to_email": "to@example.com",
                    "subject": "subj",
                    "body": "body",
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        smtp_result = await app_tool.execute(ctx, smtp_params)
        assert smtp_result.ok is False
        assert smtp_result.error_code == "profile_policy_violation"

        imap_params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "imap",
                "action": "search_messages",
                "profile_name": "default",
                "params": {"query": "ALL"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        imap_result = await app_tool.execute(ctx, imap_params)
        assert imap_result.ok is False
        assert imap_result.error_code == "profile_policy_violation"
    finally:
        await engine.dispose()


async def test_app_run_rejects_legacy_top_level_fields(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should reject removed legacy envelope fields."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None
        with pytest.raises(ToolParametersValidationError) as exc_info:
            app_tool.parse_params(
                {
                    "profile_key": "default",
                    "app_name": "telegram",
                    "action": "get_me",
                    "profile_name": "default",
                    "skill_name": "telegram",
                    "parameters": {},
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            )

        assert exc_info.value.error_code == "app_run_invalid"
        assert exc_info.value.metadata["unexpected_fields"] == ["parameters", "skill_name"]
    finally:
        await engine.dispose()


async def test_app_run_accepts_common_telegram_bot_api_action_aliases(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should normalize common Telegram Bot API camelCase action aliases."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        app_tool = registry.get("app.run")
        assert app_tool is not None

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="legacy-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )

        async def _fake_post_send_message(**_: object) -> dict[str, object]:
            return {"ok": True, "action": "send_message", "message_id": 7}

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "sendMessage",
                "profile_name": "default",
                "params": {"text": "hello alias"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)
        assert result.ok is True
        assert result.payload["action"] == "send_message"
    finally:
        await engine.dispose()


async def test_app_run_returns_structured_validation_hint_for_telegram_params(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram action validation errors should surface missing/allowed params clearly."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {"chat_id": "1001"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert "Missing required params: text." in str(result.reason)
        assert result.metadata["app_name"] == "telegram"
        assert result.metadata["action"] == "send_message"
        assert result.metadata["required_params"] == ["text"]
        assert "chat_id" in result.metadata["optional_params"]
        assert result.metadata["missing_params"] == ["text"]
    finally:
        await engine.dispose()


async def test_app_run_telegram_rejects_whitespace_only_send_message(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram send_message should reject whitespace-only text instead of succeeding silently."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
        app_tool = registry.get("app.run")
        assert app_tool is not None
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {"text": "   "},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert "must contain non-whitespace content" in str(result.reason)
    finally:
        await engine.dispose()


async def test_app_run_telegram_supports_message_thread_id(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram send_message should accept optional message_thread_id for threaded delivery."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
        app_tool = registry.get("app.run")
        assert app_tool is not None
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )

        calls: list[dict[str, object]] = []

        async def _fake_post_send_message(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "action": "send_message",
                "message_id": 778,
                "chat_id": "1001",
                "message_thread_id": 9001,
            }

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {
                    "text": "threaded",
                    "message_thread_id": 9001,
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is True
        assert result.payload["message_thread_id"] == 9001
        assert calls[0]["message_thread_id"] == 9001
    finally:
        await engine.dispose()


async def test_app_run_telegram_splits_long_send_message(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram app should split oversized send_message payloads into safe chunks."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
        app_tool = registry.get("app.run")
        assert app_tool is not None
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )

        calls: list[dict[str, object]] = []

        async def _fake_post_send_message(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "action": "send_message",
                "message_id": len(calls),
                "chat_id": "1001",
            }

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {
                    "text": ("alpha " * 900).strip(),
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is True
        assert result.payload["chunk_count"] == 2
        assert len(calls) == 2
        assert all(len(str(item["text"])) <= 4096 for item in calls)
    finally:
        await engine.dispose()


async def test_app_run_telegram_supports_send_document(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram app should expose send_document for skill-driven file delivery."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
        app_tool = registry.get("app.run")
        assert app_tool is not None
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )
        document_path = tmp_path / "note.txt"
        document_path.write_text("hello", encoding="utf-8")
        calls: list[dict[str, object]] = []

        async def _fake_post_send_media(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {"ok": True, "action": "send_document", "message_id": 991, "chat_id": "1001"}

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_media",
            _fake_post_send_media,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_document",
                "profile_name": "default",
                "params": {
                    "document": str(document_path.relative_to(tmp_path)),
                    "caption": "держи",
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is True
        assert calls[0]["action"] == "send_document"
        assert calls[0]["field_name"] == "document"
        assert calls[0]["media_value"] == str(document_path.relative_to(tmp_path))
    finally:
        await engine.dispose()


async def test_telegram_media_path_defaults_to_profile_workspace(tmp_path: Path) -> None:
    """Telegram media helper should resolve relative files from the active profile workspace."""

    settings = Settings(root_dir=tmp_path)
    profile_docs = tmp_path / "profiles/default/docs"
    profile_docs.mkdir(parents=True)
    expected = profile_docs / "report.txt"
    expected.write_text("report", encoding="utf-8")

    resolved = await _resolve_workspace_media_path(
        settings=settings,
        profile_id="default",
        raw_value="docs/report.txt",
    )
    assert resolved == expected


async def test_telegram_media_path_resolves_bare_filename_from_profile_workspace(
    tmp_path: Path,
) -> None:
    """Telegram media helper should resolve bare filenames from the profile workspace root."""

    settings = Settings(root_dir=tmp_path)
    expected = tmp_path / "profiles/default" / "report.txt"
    expected.parent.mkdir(parents=True)
    expected.write_text("report", encoding="utf-8")

    resolved = await _resolve_workspace_media_path(
        settings=settings,
        profile_id="default",
        raw_value="report.txt",
    )
    assert resolved == expected


async def test_telegram_media_path_rejects_outside_hard_workspace_override(tmp_path: Path) -> None:
    """Telegram media helper should fail closed when shared hard workspace override excludes the file."""

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, tool_workspace_root=shared_root)

    with pytest.raises(ValueError, match="outside allowed workspace scope"):
        await _resolve_workspace_media_path(
            settings=settings,
            profile_id="default",
            raw_value=str(outside_file),
        )


async def test_telegram_media_path_reports_missing_file(tmp_path: Path) -> None:
    """Telegram media helper should distinguish a missing file from a scope violation."""

    settings = Settings(root_dir=tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        await _resolve_workspace_media_path(
            settings=settings,
            profile_id="default",
            raw_value="docs/missing.txt",
        )


async def test_telegram_media_path_treats_slashed_file_id_as_remote_reference(
    tmp_path: Path,
) -> None:
    """Telegram media helper should not mistake opaque file ids for local workspace paths."""

    settings = Settings(root_dir=tmp_path)

    resolved = await _resolve_workspace_media_path(
        settings=settings,
        profile_id="default",
        raw_value="AgACAgIAAxkBAAIBQGX2/file-id-with-slash",
    )

    assert resolved is None


async def test_app_run_telegram_supports_send_chat_action(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram app should support send_chat_action for typing indicators."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
        app_tool = registry.get("app.run")
        assert app_tool is not None
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="1001",
        )

        calls: list[dict[str, object]] = []

        async def _fake_post_send_chat_action(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "action": "send_chat_action",
                "chat_id": "1001",
                "message_thread_id": 9001,
                "chat_action": "typing",
            }

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_chat_action",
            _fake_post_send_chat_action,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_chat_action",
                "profile_name": "default",
                "params": {
                    "action": "typing",
                    "message_thread_id": 9001,
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is True
        assert result.payload["action"] == "send_chat_action"
        assert calls == [
            {
                "token": "test-telegram-token",
                "chat_id": "1001",
                "action": "typing",
                "message_thread_id": 9001,
                "timeout_sec": settings.tool_timeout_default_sec,
            }
        ]
    finally:
        await engine.dispose()


async def test_app_run_telegram_supports_ban_chat_member(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram moderation path should support banning one chat member."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
        app_tool = registry.get("app.run")
        assert app_tool is not None
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_token",
            value="test-telegram-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="default",
            credential_slug="telegram_chat_id",
            value="-100200300",
        )

        calls: list[dict[str, object]] = []

        async def _fake_post_ban_chat_member(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "action": "ban_chat_member",
                "chat_id": "-100200300",
                "user_id": 777,
                "revoke_messages": True,
                "until_date": 1234567890,
            }

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_ban_chat_member",
            _fake_post_ban_chat_member,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "ban_chat_member",
                "profile_name": "default",
                "params": {
                    "user_id": 777,
                    "revoke_messages": True,
                    "until_date": 1234567890,
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is True
        assert result.payload["action"] == "ban_chat_member"
        assert calls[0]["chat_id"] == "-100200300"
        assert calls[0]["user_id"] == 777
        assert calls[0]["revoke_messages"] is True
        assert calls[0]["until_date"] == 1234567890
    finally:
        await engine.dispose()


async def test_app_run_telegram_returns_structured_validation_hint_for_ban_params(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram moderation validation should surface missing user_id clearly."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "ban_chat_member",
                "profile_name": "default",
                "params": {"chat_id": "-100200300"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert "Missing required params: user_id." in str(result.reason)
        assert result.metadata["action"] == "ban_chat_member"
        assert result.metadata["required_params"] == ["user_id"]
    finally:
        await engine.dispose()


async def test_app_run_returns_structured_validation_hint_for_smtp_params(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """SMTP action validation errors should list missing required email fields."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "smtp",
                "action": "send_email",
                "profile_name": "default",
                "params": {"to_email": "user@example.com", "subject": "Hello"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert "Missing required params: body." in str(result.reason)
        assert result.metadata["required_params"] == ["body", "subject", "to_email"]
        assert result.metadata["missing_params"] == ["body"]
    finally:
        await engine.dispose()


async def test_app_run_returns_structured_validation_hint_for_imap_params(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """IMAP action validation errors should report unexpected params and allowed keys."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        app_tool = registry.get("app.run")
        assert app_tool is not None

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "imap",
                "action": "search_messages",
                "profile_name": "default",
                "params": {"folder": "INBOX"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(
            ToolContext(profile_id="default", session_id="s", run_id=1), params
        )

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert "Unexpected params: folder." in str(result.reason)
        assert result.metadata["required_params"] == []
        assert "query" in result.metadata["optional_params"]
        assert result.metadata["unexpected_params"] == ["folder"]
    finally:
        await engine.dispose()


async def test_app_run_returns_structured_validation_hint_for_top_level_fields(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Top-level app.run envelope errors should expose missing/allowed fields to the model."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        runtime = ToolExecutionRuntime(
            tool_registry=registry,
            actor="main",
            policy_engine=object(),
            security_guard=object(),
            safety_policy=object(),
            tool_invocation_gates=object(),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
            log_event=_noop_async,
            raise_if_cancel_requested=_noop_async,
            sanitize=lambda value: value,
            sanitize_value=lambda value: value,
            to_params_dict=lambda value: cast(dict[str, object], value),
            tool_log_payload=lambda **_: {},
        )

        result = await runtime.execute_tool_call(
            tool_call=ToolCall(
                name="app.run",
                params={
                    "profile_key": "default",
                    "app_name": "telegram",
                    "params": {"text": "hello"},
                },
            ),
            ctx=ToolContext(profile_id="default", session_id="s", run_id=1),
        )

        assert result.ok is False
        assert result.error_code == "app_run_invalid"
        assert "Missing required fields: action." in str(result.reason)
        assert result.metadata["tool_name"] == "app.run"
        assert result.metadata["required_fields"] == ["app_name", "action"]
        assert result.metadata["missing_fields"] == ["action"]
        assert "params" in result.metadata["allowed_fields"]
    finally:
        await engine.dispose()


async def test_app_run_auto_picks_single_non_default_credential_profile(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should auto-resolve the only available credential profile when omitted."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        app_tool = registry.get("app.run")
        assert app_tool is not None

        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="work",
            credential_slug="telegram_token",
            value="work-token",
        )
        await _create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="telegram",
            profile_name="work",
            credential_slug="telegram_chat_id",
            value="2002",
        )

        async def _fake_post_send_message(**_: object) -> dict[str, object]:
            return {"ok": True, "action": "send_message", "message_id": 22, "chat_id": "2002"}

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "params": {"text": "hello work profile"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)
        assert result.ok is True
        assert result.payload["message_id"] == 22
    finally:
        await engine.dispose()


async def test_app_run_resolves_secret_placeholders_in_params_and_credential_names(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should resolve `${{CRED:...}}` placeholders in params and *_credential_name fields."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        app_tool = registry.get("app.run")
        assert app_tool is not None

        for slug, value in (
            ("telegram_token", "token-placeholder"),
            ("telegram_chat_id", "2001"),
            ("telegram_message", "hello from placeholder"),
        ):
            await _create_credential(
                registry=registry,
                settings=settings,
                ctx=ctx,
                app_name="telegram",
                profile_name="default",
                credential_slug=slug,
                value=value,
            )

        captured: dict[str, object] = {}

        async def _fake_post_send_message(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"ok": True, "action": "send_message", "message_id": 9}

        monkeypatch.setattr(
            "afkbot.services.apps.telegram.actions._post_send_message",
            _fake_post_send_message,
        )

        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {
                    "text": "${{CRED:telegram_message}}",
                    "chat_id": "${{CRED:telegram_chat_id}}",
                    "token_credential_name": "${{CRED:telegram_token}}",
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)

        assert result.ok is True
        assert captured["token"] == "token-placeholder"
        assert captured["chat_id"] == "2001"
        assert captured["text"] == "hello from placeholder"
    finally:
        await engine.dispose()


async def test_app_run_rejects_legacy_credential_placeholders_in_params(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """app.run should reject removed legacy credential placeholder syntax."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    try:
        await _set_network_allowlist(
            settings=settings,
            profile_id="default",
            hosts=["api.telegram.org"],
        )
        app_tool = registry.get("app.run")
        assert app_tool is not None

        for slug, value in (
            ("telegram_token", "legacy-token"),
            ("telegram_chat_id", "3001"),
        ):
            await _create_credential(
                registry=registry,
                settings=settings,
                ctx=ctx,
                app_name="telegram",
                profile_name="default",
                credential_slug=slug,
                value=value,
            )
        params = app_tool.parse_params(
            {
                "profile_key": "default",
                "app_name": "telegram",
                "action": "send_message",
                "profile_name": "default",
                "params": {
                    "text": "{{credential:telegram_message}}",
                    "chat_id": "{{credential:default/telegram_chat_id}}",
                    "token_credential_name": "{{credential:default/telegram_token}}",
                },
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await app_tool.execute(ctx, params)
        assert result.ok is False
        assert result.error_code == "credentials_invalid_name"
        assert "Unsupported credential placeholder syntax" in str(result.reason)
    finally:
        await engine.dispose()


def test_smtp_action_sync_uses_tls_context(monkeypatch: MonkeyPatch) -> None:
    """SMTP sync implementation should pass TLS verification context."""

    captured: dict[str, Any] = {"ssl_context": None, "starttls_context": None}

    class _FakeSMTP:
        def __init__(self, *, host: str, port: int, timeout: float) -> None:  # noqa: ARG002
            self._messages: list[EmailMessage] = []

        def __enter__(self) -> "_FakeSMTP":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:  # noqa: ARG002
            return False

        def starttls(self, *, context: object) -> None:
            captured["starttls_context"] = context

        def login(self, username: str, password: str) -> None:  # noqa: ARG002
            return None

        def send_message(self, message: EmailMessage) -> None:
            self._messages.append(message)

    class _FakeSMTPSSL(_FakeSMTP):
        def __init__(self, *, host: str, port: int, timeout: float, context: object) -> None:
            super().__init__(host=host, port=port, timeout=timeout)
            captured["ssl_context"] = context

    monkeypatch.setattr("afkbot.services.apps.smtp.actions.smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("afkbot.services.apps.smtp.actions.smtplib.SMTP_SSL", _FakeSMTPSSL)

    _send_email_sync(
        host="smtp.example.com",
        port=587,
        username="u",
        password="p",
        from_email="from@example.com",
        to_email="to@example.com",
        subject="subject",
        body="body",
        content_type="plain",
        use_tls=True,
        use_ssl=True,
        timeout_sec=10,
    )
    assert captured["ssl_context"] is not None

    _send_email_sync(
        host="smtp.example.com",
        port=587,
        username="u",
        password="p",
        from_email="from@example.com",
        to_email="to@example.com",
        subject="subject",
        body="body",
        content_type="plain",
        use_tls=True,
        use_ssl=False,
        timeout_sec=10,
    )
    assert captured["starttls_context"] is not None


def test_imap_action_sync_uses_ssl_context(monkeypatch: MonkeyPatch) -> None:
    """IMAP SSL path should pass verification context into IMAP4_SSL."""

    captured: dict[str, Any] = {"ssl_context": None}

    class _FakeIMAP:
        def __enter__(self) -> "_FakeIMAP":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:  # noqa: ARG002
            return False

        def login(self, username: str, password: str) -> tuple[str, list[bytes]]:  # noqa: ARG002
            return ("OK", [])

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:  # noqa: ARG002
            return ("OK", [])

        def search(self, charset: object, query: str) -> tuple[str, list[bytes]]:  # noqa: ARG002
            return ("OK", [b""])

    class _FakeIMAPSSL(_FakeIMAP):
        def __init__(self, *, host: str, port: int, timeout: float, ssl_context: object) -> None:  # noqa: ARG002
            captured["ssl_context"] = ssl_context

    monkeypatch.setattr("afkbot.services.apps.imap.actions.imaplib.IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr("afkbot.services.apps.imap.actions.imaplib.IMAP4", _FakeIMAP)

    result = _search_messages_sync(
        host="imap.example.com",
        port=993,
        username="u",
        password="p",
        mailbox="INBOX",
        query="ALL",
        limit=10,
        use_ssl=True,
        timeout_sec=10,
    )
    assert cast(list[dict[str, object]], result) == []
    assert captured["ssl_context"] is not None
