"""CRUD-focused tests for the automation service."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.channels import ChannelDeliveryTarget
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
            delivery_target=ChannelDeliveryTarget(
                transport="smtp",
                address="ops@example.com",
                subject="Cron digest",
            ),
        )
        webhook = await service.create_webhook(
            profile_id="default",
            name="webhook job",
            prompt="do webhook",
            delivery_target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="42",
            ),
        )

        listed = await service.list(profile_id="default")
        assert len(listed) == 2
        assert {item.id for item in listed} == {cron.id, webhook.id}
        assert cron.delivery_mode == "target"
        assert cron.delivery_target is not None
        assert cron.delivery_target.model_dump(exclude_none=True) == {
            "transport": "smtp",
            "address": "ops@example.com",
            "subject": "Cron digest",
        }
        assert webhook.delivery_mode == "target"
        assert webhook.delivery_target is not None
        assert webhook.delivery_target.model_dump(exclude_none=True) == {
            "transport": "telegram",
            "peer_id": "42",
        }

        fetched = await service.get(profile_id="default", automation_id=cron.id)
        assert fetched.id == cron.id
        assert fetched.cron is not None
        assert fetched.webhook is None
        assert fetched.delivery_mode == "target"
        assert fetched.delivery_target is not None
        assert fetched.delivery_target.model_dump(exclude_none=True) == {
            "transport": "smtp",
            "address": "ops@example.com",
            "subject": "Cron digest",
        }

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


async def test_service_rejects_invalid_telegram_delivery_target(tmp_path: Path) -> None:
    """Telegram automations must not persist delivery targets without a peer id."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.create_cron(
                profile_id="default",
                name="bad target",
                prompt="do cron",
                cron_expr="*/5 * * * *",
                timezone_name="UTC",
                delivery_target=ChannelDeliveryTarget(
                    transport="telegram",
                    user_id="123456789",
                ),
            )
        assert exc_info.value.error_code == "channel_delivery_target_incomplete"
    finally:
        await engine.dispose()


async def test_service_supports_tool_delivery_mode_without_target(tmp_path: Path) -> None:
    """Tool delivery mode should allow automation-side messaging without persisted target."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_cron(
            profile_id="default",
            name="tool mode",
            prompt="Use app.run to send a Telegram message with text ПРИВЕТ.",
            cron_expr="*/5 * * * *",
            timezone_name="UTC",
            delivery_mode="tool",
        )
        assert created.delivery_mode == "tool"
        assert created.delivery_target is None
        assert "Execution hints:" in created.prompt
        assert "app.run with the Telegram app" in created.prompt
    finally:
        await engine.dispose()


async def test_service_normalizes_prompt_when_switching_to_tool_delivery_mode(tmp_path: Path) -> None:
    """Updating automation to tool mode should enrich prompt with execution hints."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_cron(
            profile_id="default",
            name="tool mode later",
            prompt="Отправить сообщение ПРИВЕТ через Telegram.",
            cron_expr="*/5 * * * *",
            timezone_name="UTC",
            delivery_target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="42",
            ),
        )
        updated = await service.update(
            profile_id="default",
            automation_id=created.id,
            delivery_mode="tool",
            clear_delivery_target=True,
        )
        assert updated.delivery_mode == "tool"
        assert updated.delivery_target is None
        assert "Execution hints:" in updated.prompt
        assert "app.run with the Telegram app" in updated.prompt
    finally:
        await engine.dispose()


async def test_service_strips_managed_hints_when_switching_back_to_target_mode(tmp_path: Path) -> None:
    """Switching away from tool delivery should drop managed execution hints."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_cron(
            profile_id="default",
            name="tool mode cleanup",
            prompt="Отправить сообщение ПРИВЕТ через Telegram.",
            cron_expr="*/5 * * * *",
            timezone_name="UTC",
            delivery_mode="tool",
        )
        assert "Execution hints:" in created.prompt
        updated = await service.update(
            profile_id="default",
            automation_id=created.id,
            delivery_mode="target",
            delivery_target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="42",
            ),
        )
        assert updated.delivery_mode == "target"
        assert updated.delivery_target is not None
        assert "Execution hints:" not in updated.prompt
        assert updated.prompt == "Отправить сообщение ПРИВЕТ через Telegram."
    finally:
        await engine.dispose()
