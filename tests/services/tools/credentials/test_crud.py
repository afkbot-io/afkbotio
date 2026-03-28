"""CRUD and listing scenarios for credentials plugins."""

from __future__ import annotations

import json
from typing import Any, cast

from pytest import MonkeyPatch

from afkbot.services.tools.base import ToolContext
from tests.services.tools.credentials._harness import (
    create_credential,
    prepare_credentials_tools,
)


async def test_credentials_plugins_crud_and_no_plaintext(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    create_tool = registry.get("credentials.create")
    assert create_tool is not None
    create_params = create_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "work",
            "credential_slug": "telegram_token",
            "value": "plaintext-secret",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    create_result = await create_tool.execute(ctx, create_params)
    assert create_result.ok is True
    assert "plaintext-secret" not in json.dumps(create_result.model_dump())

    update_tool = registry.get("credentials.update")
    assert update_tool is not None
    update_params = update_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "work",
            "credential_slug": "telegram_token",
            "value": "rotated-secret",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    update_result = await update_tool.execute(ctx, update_params)
    assert update_result.ok is True
    assert "rotated-secret" not in json.dumps(update_result.model_dump())

    list_tool = registry.get("credentials.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "work",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    assert "plaintext-secret" not in json.dumps(list_result.model_dump())
    bindings = cast(list[dict[str, Any]], list_result.payload["bindings"])
    assert len(bindings) == 1
    assert bindings[0]["tool_name"] == "app.run"
    assert bindings[0]["integration_name"] == "telegram"
    assert bindings[0]["credential_profile_key"] == "work"
    assert bindings[0]["credential_name"] == "telegram_token"
    assert bindings[0]["APP_NAME"] == "telegram"
    assert bindings[0]["PROFILE_NAME"] == "work"
    assert bindings[0]["CREDENTIAL_SLUG"] == "telegram_token"
    assert bindings[0]["ENV_KEY"] == "CRED_TELEGRAM_WORK_TELEGRAM_TOKEN"

    delete_tool = registry.get("credentials.delete")
    assert delete_tool is not None
    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "work",
            "credential_slug": "telegram_token",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    delete_result = await delete_tool.execute(ctx, delete_params)
    assert delete_result.ok is True

    listed_after_delete = await list_tool.execute(ctx, list_params)
    assert listed_after_delete.ok is True
    assert listed_after_delete.payload["bindings"] == []

    inactive_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
            "profile_name": "work",
            "include_inactive": True,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    listed_inactive = await list_tool.execute(ctx, inactive_params)
    assert listed_inactive.ok is True
    inactive_bindings = cast(list[dict[str, Any]], listed_inactive.payload["bindings"])
    assert len(inactive_bindings) == 1
    assert inactive_bindings[0]["is_active"] is False

    await engine.dispose()


async def test_credentials_plugins_profile_key_mismatch(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    create_tool = registry.get("credentials.create")
    assert create_tool is not None

    params = create_tool.parse_params(
        {
            "profile_key": "other",
            "app_name": "smtp",
            "profile_name": "default",
            "credential_slug": "smtp_password",
            "value": "plain",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await create_tool.execute(ctx, params)

    assert result.ok is False
    assert result.error_code == "profile_not_found"

    await engine.dispose()


async def test_credentials_list_filters_by_app_and_profile(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="smtp",
        profile_name="default",
        credential_slug="smtp_host",
        value="smtp.default.example.com",
    )
    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="smtp",
        profile_name="work",
        credential_slug="smtp_host",
        value="smtp.work.example.com",
    )
    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="imap",
        profile_name="default",
        credential_slug="imap_host",
        value="imap.example.com",
    )

    list_tool = registry.get("credentials.list")
    assert list_tool is not None

    smtp_all_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "smtp",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    smtp_all = await list_tool.execute(ctx, smtp_all_params)
    assert smtp_all.ok is True
    smtp_bindings = cast(list[dict[str, Any]], smtp_all.payload["bindings"])
    assert len(smtp_bindings) == 2

    smtp_work_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "smtp",
            "profile_name": "work",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    smtp_work = await list_tool.execute(ctx, smtp_work_params)
    assert smtp_work.ok is True
    smtp_work_bindings = cast(list[dict[str, Any]], smtp_work.payload["bindings"])
    assert len(smtp_work_bindings) == 1
    assert smtp_work_bindings[0]["credential_profile_key"] == "work"

    all_params = list_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    all_results = await list_tool.execute(ctx, all_params)
    assert all_results.ok is True
    all_bindings = cast(list[dict[str, Any]], all_results.payload["bindings"])
    assert len(all_bindings) == 3

    await engine.dispose()


async def test_credentials_plugins_support_backward_alias_params(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    create_tool = registry.get("credentials.create")
    update_tool = registry.get("credentials.update")
    delete_tool = registry.get("credentials.delete")
    list_tool = registry.get("credentials.list")
    assert create_tool is not None
    assert update_tool is not None
    assert delete_tool is not None
    assert list_tool is not None

    create_params = create_tool.parse_params(
        {
            "profile_key": "default",
            "integration_name": "telegram",
            "credential_profile_key": "legacy",
            "credential_name": "telegram_chat_id",
            "secret_value": "1001",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    created = await create_tool.execute(ctx, create_params)
    assert created.ok is True

    update_params = update_tool.parse_params(
        {
            "profile_key": "default",
            "integration_name": "telegram",
            "credential_profile_key": "legacy",
            "credential_name": "telegram_chat_id",
            "secret_value": "1002",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    updated = await update_tool.execute(ctx, update_params)
    assert updated.ok is True

    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "integration_name": "telegram",
            "credential_profile_key": "legacy",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    listed = await list_tool.execute(ctx, list_params)
    assert listed.ok is True
    bindings = cast(list[dict[str, Any]], listed.payload["bindings"])
    assert len(bindings) == 1
    assert bindings[0]["credential_profile_key"] == "legacy"

    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "integration_name": "telegram",
            "credential_profile_key": "legacy",
            "credential_name": "telegram_chat_id",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    deleted = await delete_tool.execute(ctx, delete_params)
    assert deleted.ok is True

    await engine.dispose()


async def test_credentials_list_includes_global_fallback_bindings_for_app_runtime(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="global",
        profile_name="work",
        credential_slug="shared_token",
        value="shared-work-token",
    )

    list_tool = registry.get("credentials.list")
    assert list_tool is not None

    params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "telegram",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    listed = await list_tool.execute(ctx, params)
    assert listed.ok is True
    bindings = cast(list[dict[str, Any]], listed.payload["bindings"])
    assert len(bindings) == 1
    assert bindings[0]["integration_name"] == "global"
    assert bindings[0]["credential_profile_key"] == "work"
    assert bindings[0]["credential_name"] == "shared_token"

    await engine.dispose()
