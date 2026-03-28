"""credentials.request plugin scenarios."""

from __future__ import annotations

from typing import Any, cast

from pytest import MonkeyPatch

from afkbot.services.tools.base import ToolContext
from tests.services.tools.credentials._harness import (
    create_credential,
    prepare_credentials_tools,
)


async def test_credentials_request_missing_then_store(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(
        tmp_path,
        monkeypatch,
        extra_plugins=("credentials_request",),
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    request_tool = registry.get("credentials.request")
    assert request_tool is not None

    missing_params = request_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "default",
            "credential_slug": "telegram_token",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    missing = await request_tool.execute(ctx, missing_params)
    assert missing.ok is False
    assert missing.error_code == "credentials_missing"
    assert missing.metadata["integration_name"] == "telegram"
    assert missing.metadata["credential_profile_key"] == "default"
    assert missing.metadata["credential_name"] == "telegram_token"

    store_params = request_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "default",
            "credential_slug": "telegram_token",
            "value": "token-123",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    stored = await request_tool.execute(ctx, store_params)
    assert stored.ok is True
    assert stored.payload["stored"] is True
    binding = cast(dict[str, Any], stored.payload["binding"])
    assert binding["ENV_KEY"] == "CRED_TELEGRAM_DEFAULT_TELEGRAM_TOKEN"

    exists = await request_tool.execute(ctx, missing_params)
    assert exists.ok is True
    assert exists.payload["exists"] is True

    await engine.dispose()


async def test_credentials_request_auto_picks_single_profile_when_profile_omitted(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(
        tmp_path,
        monkeypatch,
        extra_plugins=("credentials_request",),
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    request_tool = registry.get("credentials.request")
    assert request_tool is not None

    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="telegram",
        profile_name="work",
        credential_slug="telegram_token",
        value="token-work",
    )

    params = request_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "credential_slug": "telegram_token",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await request_tool.execute(ctx, params)
    assert result.ok is True
    binding = cast(dict[str, Any], result.payload["binding"])
    assert binding["credential_profile_key"] == "work"

    await engine.dispose()


async def test_credentials_request_lookup_uses_app_runtime_global_fallback(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(
        tmp_path,
        monkeypatch,
        extra_plugins=("credentials_request",),
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    request_tool = registry.get("credentials.request")
    assert request_tool is not None

    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="global",
        profile_name="default",
        credential_slug="telegram_token",
        value="global-token",
    )

    params = request_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "credential_slug": "telegram_token",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await request_tool.execute(ctx, params)
    assert result.ok is True
    binding = cast(dict[str, Any], result.payload["binding"])
    assert binding["integration_name"] == "global"
    assert binding["credential_profile_key"] == "default"

    await engine.dispose()


async def test_credentials_request_auto_picks_single_non_default_profile(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(
        tmp_path,
        monkeypatch,
        extra_plugins=("credentials_request",),
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    request_tool = registry.get("credentials.request")
    assert request_tool is not None

    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="telegram",
        profile_name="work",
        credential_slug="telegram_token",
        value="token-work",
    )

    exists_params = request_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "credential_slug": "telegram_token",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    exists = await request_tool.execute(ctx, exists_params)
    assert exists.ok is True
    assert exists.payload["exists"] is True
    binding = cast(dict[str, Any], exists.payload["binding"])
    assert binding["credential_profile_key"] == "work"

    store_params = request_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "credential_slug": "telegram_chat_id",
            "value": "4004",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    stored = await request_tool.execute(ctx, store_params)
    assert stored.ok is True
    stored_binding = cast(dict[str, Any], stored.payload["binding"])
    assert stored_binding["credential_profile_key"] == "work"

    await engine.dispose()
