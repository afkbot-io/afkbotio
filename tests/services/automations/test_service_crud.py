"""CRUD-focused tests for the automation service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from afkbot.services.automations import AutomationsServiceError
import afkbot.services.automations.service as service_module
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


async def test_service_reveal_webhook_endpoint_recovers_url_without_reissuing_token(
    tmp_path: Path,
) -> None:
    """Operator-only reveal should recover the current webhook URL without token rotation."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="recoverable webhook",
            prompt="handle webhook",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        fetched = await service.get(profile_id="default", automation_id=created.id)
        assert fetched.webhook is not None
        assert fetched.webhook.webhook_token is None
        assert fetched.webhook.webhook_path is None
        assert fetched.webhook.webhook_url is None
        assert fetched.webhook.webhook_token_masked == "[HIDDEN]"

        reveal = await service.reveal_webhook_endpoint(
            profile_id="default",
            automation_id=created.id,
        )
        assert reveal.recoverable is True
        assert reveal.webhook_path == created.webhook.webhook_path
        assert reveal.webhook_url == created.webhook.webhook_url
        assert reveal.webhook_token_masked != "[HIDDEN]"
    finally:
        await engine.dispose()


async def test_service_create_rejects_unimplemented_branch_error_only_mode(tmp_path: Path) -> None:
    """Create should reject fallback modes that are not implemented at runtime."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.create_webhook(
                profile_id="default",
                name="invalid-fallback",
                prompt="prompt",
                graph_fallback_mode="branch_error_only",
            )
        assert exc_info.value.error_code == "invalid_graph_fallback_mode"
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


async def test_service_create_cron_uses_timezone_for_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cron creation should calculate next_run_at from the configured local timezone."""

    fixed_now = datetime.fromisoformat("2026-03-12T15:59:59+00:00")

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(service_module, "datetime", _FixedDateTime)
    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_cron(
            profile_id="default",
            name="berlin cron",
            prompt="run at berlin morning",
            cron_expr="0 9 * * *",
            timezone_name="Europe/Berlin",
        )
        assert created.cron is not None
        assert created.cron.timezone == "Europe/Berlin"
        assert created.cron.next_run_at == datetime.fromisoformat("2026-03-13T08:00:00")
    finally:
        await engine.dispose()
