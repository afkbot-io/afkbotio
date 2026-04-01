"""CRUD-focused tests for the automation service."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.automations import AutomationsServiceError
from tests.services.automations._harness import prepare_service


async def test_service_create_list_get_delete(tmp_path: Path) -> None:
    """Service should support create/list/get/delete for profile automations."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        cron = await service.create_cron(
            profile_id="default",
            name="cron job",
            prompt="do cron",
            cron_expr="* * * * *",
            timezone_name="UTC",
        )
        webhook = await service.create_webhook(
            profile_id="default",
            name="webhook job",
            prompt="do webhook",
        )

        listed = await service.list(profile_id="default")
        assert len(listed) == 2
        assert {item.id for item in listed} == {cron.id, webhook.id}
        assert cron.trigger_type == "cron"
        assert webhook.trigger_type == "webhook"

        fetched = await service.get(profile_id="default", automation_id=cron.id)
        assert fetched.id == cron.id
        assert fetched.cron is not None
        assert fetched.webhook is None

        deleted = await service.delete(profile_id="default", automation_id=cron.id)
        assert deleted is True

        with pytest.raises(AutomationsServiceError, match="Automation not found"):
            await service.get(profile_id="default", automation_id=cron.id)
    finally:
        await engine.dispose()


async def test_service_profile_isolation(tmp_path: Path) -> None:
    """Automation retrieval must stay isolated by profile id."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="private",
            prompt="private prompt",
        )

        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.get(profile_id="other", automation_id=created.id)
        assert exc_info.value.error_code == "automation_not_found"
    finally:
        await engine.dispose()


async def test_service_preserves_literal_prompt_body(tmp_path: Path) -> None:
    """Service should store prompt body as-is after whitespace normalization."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_cron(
            profile_id="default",
            name="managed-hints",
            prompt="Run audit\n\nExecution hints:\n- stale hint",
            cron_expr="*/5 * * * *",
            timezone_name="UTC",
        )
        assert created.prompt == "Run audit\n\nExecution hints:\n- stale hint"

        updated = await service.update(
            profile_id="default",
            automation_id=created.id,
            prompt="Run audit again\n\nExecution hints:\n- stale hint",
        )
        assert updated.prompt == "Run audit again\n\nExecution hints:\n- stale hint"
    finally:
        await engine.dispose()
