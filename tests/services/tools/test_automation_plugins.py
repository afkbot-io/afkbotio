"""Integration tests for automation tool plugins contract."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations import (
    AutomationsServiceError,
    get_automations_service,
    reset_automations_services,
)
from afkbot.services.automations.webhook_tokens import build_webhook_path, build_webhook_url
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


async def _prepare(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> tuple[Settings, AsyncEngine, ToolRegistry]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tools_automation.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    get_settings.cache_clear()
    reset_automations_services()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    return settings, engine, ToolRegistry.from_settings(settings)


async def test_automation_plugins_crud(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Automation plugins should support create/list/get/delete workflow."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

        create_tool = registry.get("automation.create")
        assert create_tool is not None
        create_params = create_tool.parse_params(
            {
                "profile_key": "default",
                "name": "job-1",
                "prompt": "do job",
                "trigger_type": "cron",
                "cron_expr": "* * * * *",
                "timezone": "UTC",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        create_result = await create_tool.execute(ctx, create_params)
        assert create_result.ok is True
        automation = create_result.payload["automation"]
        assert isinstance(automation, dict)
        automation_id = int(automation["id"])
        assert automation["trigger_type"] == "cron"
        assert isinstance(automation["cron"], dict)

        create_webhook_params = create_tool.parse_params(
            {
                "profile_key": "default",
                "name": "job-2",
                "prompt": "listen",
                "trigger_type": "webhook",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        create_webhook_result = await create_tool.execute(ctx, create_webhook_params)
        assert create_webhook_result.ok is True
        webhook_automation = create_webhook_result.payload["automation"]
        assert isinstance(webhook_automation, dict)
        assert isinstance(webhook_automation["webhook"], dict)
        webhook_id = int(webhook_automation["id"])
        issued_token = webhook_automation["webhook"]["webhook_token"]
        assert isinstance(issued_token, str)
        assert webhook_automation["webhook"]["webhook_path"] == build_webhook_path("default", issued_token)
        assert webhook_automation["webhook"]["webhook_url"] == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            issued_token,
        )

        list_tool = registry.get("automation.list")
        assert list_tool is not None
        list_params = list_tool.parse_params(
            {"profile_key": "default"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        list_result = await list_tool.execute(ctx, list_params)
        assert list_result.ok is True
        listed = list_result.payload["automations"]
        assert isinstance(listed, list)
        assert len(listed) == 2
        webhook_list_item = next(item for item in listed if int(item["id"]) == webhook_id)
        assert webhook_list_item["webhook"]["webhook_token"] == issued_token
        assert webhook_list_item["webhook"]["webhook_path"] == build_webhook_path("default", issued_token)
        assert webhook_list_item["webhook"]["webhook_url"] == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            issued_token,
        )

        update_tool = registry.get("automation.update")
        assert update_tool is not None
        update_params = update_tool.parse_params(
            {
                "profile_key": "default",
                "id": automation_id,
                "name": "job-1-updated",
                "status": "paused",
                "cron_expr": "0 * * * *",
                "timezone": "Europe/Berlin",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        update_result = await update_tool.execute(ctx, update_params)
        assert update_result.ok is True
        updated_automation = update_result.payload["automation"]
        assert isinstance(updated_automation, dict)
        assert updated_automation["name"] == "job-1-updated"
        assert updated_automation["status"] == "paused"
        assert isinstance(updated_automation["cron"], dict)
        assert updated_automation["cron"]["cron_expr"] == "0 * * * *"

        rotate_params = update_tool.parse_params(
            {
                "profile_key": "default",
                "id": webhook_id,
                "rotate_webhook_token": True,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        rotate_result = await update_tool.execute(ctx, rotate_params)
        assert rotate_result.ok is True
        rotated = rotate_result.payload["automation"]
        assert isinstance(rotated, dict)
        assert isinstance(rotated["webhook"], dict)
        rotated_token = rotated["webhook"]["webhook_token"]
        assert isinstance(rotated_token, str)
        assert rotated_token != issued_token
        assert rotated["webhook"]["webhook_path"] == build_webhook_path("default", rotated_token)
        assert rotated["webhook"]["webhook_url"] == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            rotated_token,
        )

        get_tool = registry.get("automation.get")
        assert get_tool is not None
        webhook_get_params = get_tool.parse_params(
            {"profile_key": "default", "id": webhook_id},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        webhook_get_result = await get_tool.execute(ctx, webhook_get_params)
        assert webhook_get_result.ok is True
        webhook_get = webhook_get_result.payload["automation"]
        assert isinstance(webhook_get, dict)
        assert webhook_get["webhook"]["webhook_token"] == rotated_token
        assert webhook_get["webhook"]["webhook_path"] == build_webhook_path("default", rotated_token)
        assert webhook_get["webhook"]["webhook_url"] == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            rotated_token,
        )

        get_params = get_tool.parse_params(
            {"profile_key": "default", "id": automation_id},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        get_result = await get_tool.execute(ctx, get_params)
        assert get_result.ok is True
        get_automation = get_result.payload["automation"]
        assert isinstance(get_automation, dict)
        assert isinstance(get_automation["webhook"], (dict, type(None)))
        if get_automation["webhook"] is not None:
            assert get_automation["webhook"]["webhook_token"] is not None
            assert get_automation["webhook"]["webhook_path"] is not None

        delete_tool = registry.get("automation.delete")
        assert delete_tool is not None
        delete_params = delete_tool.parse_params(
            {"profile_key": "default", "id": automation_id},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        delete_result = await delete_tool.execute(ctx, delete_params)
        assert delete_result.ok is True

        get_after_delete = await get_tool.execute(ctx, get_params)
        assert get_after_delete.ok is False
        assert get_after_delete.error_code == "automation_not_found"
    finally:
        await engine.dispose()


async def test_automation_plugins_profile_key_mismatch(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Mismatched profile_key should return strict profile_not_found."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
        list_tool = registry.get("automation.list")
        assert list_tool is not None

        params = list_tool.parse_params(
            {"profile_key": "other"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await list_tool.execute(ctx, params)

        assert result.ok is False
        assert result.error_code == "profile_not_found"
    finally:
        await engine.dispose()


async def test_automation_update_plugin_invalid_payload(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """automation.update should return invalid_update_payload when no fields are provided."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
        create_tool = registry.get("automation.create")
        assert create_tool is not None
        create_params = create_tool.parse_params(
            {
                "profile_key": "default",
                "name": "job-no-update",
                "prompt": "do job",
                "trigger_type": "cron",
                "cron_expr": "* * * * *",
                "timezone": "UTC",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        create_result = await create_tool.execute(ctx, create_params)
        assert create_result.ok is True
        created = create_result.payload["automation"]
        assert isinstance(created, dict)

        update_tool = registry.get("automation.update")
        assert update_tool is not None
        update_params = update_tool.parse_params(
            {"profile_key": "default", "id": int(created["id"])},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        update_result = await update_tool.execute(ctx, update_params)

        assert update_result.ok is False
        assert update_result.error_code == "invalid_update_payload"
    finally:
        await engine.dispose()


async def test_automation_update_plugin_maps_service_conflict(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """automation.update should surface service conflict as structured tool error."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
        update_tool = registry.get("automation.update")
        assert update_tool is not None
        service = get_automations_service(settings)

        async def _raise_conflict(**_: object) -> object:
            raise AutomationsServiceError(
                error_code="automation_webhook_token_conflict",
                reason="Webhook token rotation conflict",
            )

        monkeypatch.setattr(service, "update", _raise_conflict)

        update_params = update_tool.parse_params(
            {"profile_key": "default", "id": 1, "name": "noop"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        update_result = await update_tool.execute(ctx, update_params)

        assert update_result.ok is False
        assert update_result.error_code == "automation_webhook_token_conflict"
    finally:
        await engine.dispose()
