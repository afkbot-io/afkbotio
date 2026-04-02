"""Update and token issuance tests for the automation service."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations import AutomationsServiceError
from afkbot.services.automations.webhook_tokens import (
    build_webhook_path,
    build_webhook_url,
    hash_webhook_token,
)
from tests.services.automations._harness import FakeLoop, prepare_service
import afkbot.services.automations.service as service_module


async def test_service_update_cron_and_webhook_rotation(tmp_path: Path) -> None:
    """Update should modify allowed fields and rotate webhook token when requested."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created_cron = await service.create_cron(
            profile_id="default",
            name="cron-old",
            prompt="cron old prompt",
            cron_expr="* * * * *",
            timezone_name="UTC",
        )
        updated_cron = await service.update(
            profile_id="default",
            automation_id=created_cron.id,
            name="cron-new",
            prompt="cron new prompt",
            status="paused",
            cron_expr="0 * * * *",
            timezone_name="Europe/Berlin",
        )
        assert updated_cron.name == "cron-new"
        assert updated_cron.prompt == "cron new prompt"
        assert updated_cron.status == "paused"
        assert updated_cron.cron is not None
        assert updated_cron.cron.cron_expr == "0 * * * *"
        assert updated_cron.cron.timezone == "Europe/Berlin"
        assert updated_cron.cron.next_run_at is not None

        created_webhook = await service.create_webhook(
            profile_id="default",
            name="hook-old",
            prompt="hook old prompt",
        )
        assert created_webhook.webhook is not None
        old_token = created_webhook.webhook.webhook_token
        assert old_token is not None
        assert created_webhook.webhook.webhook_path == build_webhook_path("default", old_token)
        assert created_webhook.webhook.webhook_url == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            old_token,
        )
        rotated_webhook = await service.update(
            profile_id="default",
            automation_id=created_webhook.id,
            rotate_webhook_token=True,
        )
        assert rotated_webhook.webhook is not None
        new_token = rotated_webhook.webhook.webhook_token
        assert new_token is not None
        assert new_token != old_token
        assert rotated_webhook.webhook.webhook_path == build_webhook_path("default", new_token)
        assert rotated_webhook.webhook.webhook_url == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            new_token,
        )

        fake_loop = FakeLoop()

        def factory_fn(session, profile_id):
            _ = session, profile_id
            return fake_loop

        with pytest.raises(AutomationsServiceError) as old_token_exc:
            await service.trigger_webhook(
                profile_id="default",
                token=old_token,
                payload={"event_id": "evt-old-token"},
                agent_loop_factory=factory_fn,
            )
        assert old_token_exc.value.error_code == "automation_not_found"

        new_token_result = await service.trigger_webhook(
            profile_id="default",
            token=new_token,
            payload={"event_id": "evt-new-token"},
            agent_loop_factory=factory_fn,
        )
        assert new_token_result.deduplicated is False
    finally:
        await engine.dispose()


async def test_service_update_validation_errors(tmp_path: Path) -> None:
    """Update should reject empty and trigger-incompatible payloads."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created_cron = await service.create_cron(
            profile_id="default",
            name="cron-val",
            prompt="cron val prompt",
            cron_expr="* * * * *",
            timezone_name="UTC",
        )
        created_webhook = await service.create_webhook(
            profile_id="default",
            name="hook-val",
            prompt="hook val prompt",
        )

        with pytest.raises(AutomationsServiceError) as empty_exc:
            await service.update(
                profile_id="default",
                automation_id=created_cron.id,
            )
        assert empty_exc.value.error_code == "invalid_update_payload"

        with pytest.raises(AutomationsServiceError) as status_exc:
            await service.update(
                profile_id="default",
                automation_id=created_cron.id,
                status="deleted",
            )
        assert status_exc.value.error_code == "invalid_status"

        with pytest.raises(AutomationsServiceError) as rotate_cron_exc:
            await service.update(
                profile_id="default",
                automation_id=created_cron.id,
                rotate_webhook_token=True,
            )
        assert rotate_cron_exc.value.error_code == "invalid_update_payload"

        with pytest.raises(AutomationsServiceError) as cron_webhook_exc:
            await service.update(
                profile_id="default",
                automation_id=created_webhook.id,
                cron_expr="* * * * *",
            )
        assert cron_webhook_exc.value.error_code == "invalid_update_payload"

        await service.delete(profile_id="default", automation_id=created_cron.id)
        with pytest.raises(AutomationsServiceError) as deleted_exc:
            await service.update(
                profile_id="default",
                automation_id=created_cron.id,
                name="cannot-update-deleted",
            )
        assert deleted_exc.value.error_code == "invalid_status"

        with pytest.raises(AutomationsServiceError) as invalid_prompt_exc:
            await service.update(
                profile_id="default",
                automation_id=created_webhook.id,
                prompt="   ",
            )
        assert invalid_prompt_exc.value.error_code == "invalid_prompt"
    finally:
        await engine.dispose()


async def test_service_update_trigger_only_touches_updated_at_and_maps_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trigger-only update should touch automation timestamp and map token conflicts."""

    engine, factory, service = await prepare_service(tmp_path)
    try:
        created_cron = await service.create_cron(
            profile_id="default",
            name="cron-touch",
            prompt="cron touch prompt",
            cron_expr="* * * * *",
            timezone_name="UTC",
        )
        updated_cron = await service.update(
            profile_id="default",
            automation_id=created_cron.id,
            cron_expr="0 * * * *",
        )
        assert updated_cron.updated_at > created_cron.updated_at

        same_name_cron = await service.create_cron(
            profile_id="default",
            name="cron-same",
            prompt="cron same prompt",
            cron_expr="* * * * *",
            timezone_name="UTC",
        )
        updated_same_name_cron = await service.update(
            profile_id="default",
            automation_id=same_name_cron.id,
            name="cron-same",
            cron_expr="15 * * * *",
        )
        assert updated_same_name_cron.updated_at > same_name_cron.updated_at

        first_webhook = await service.create_webhook(
            profile_id="default",
            name="hook-touch-1",
            prompt="hook touch prompt 1",
        )
        second_webhook = await service.create_webhook(
            profile_id="default",
            name="hook-touch-2",
            prompt="hook touch prompt 2",
        )
        assert first_webhook.webhook is not None
        first_before_rotate = first_webhook.updated_at
        rotated = await service.update(
            profile_id="default",
            automation_id=first_webhook.id,
            rotate_webhook_token=True,
        )
        assert rotated.updated_at > first_before_rotate

        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            row = await repo.get_by_id(
                profile_id="default",
                automation_id=second_webhook.id,
            )
            assert row is not None
            assert row[2] is not None
            existing_hash = row[2].webhook_token_hash

        monkeypatch.setattr(service_module, "hash_webhook_token", lambda _token: existing_hash)
        with pytest.raises(AutomationsServiceError) as conflict_exc:
            await service.update(
                profile_id="default",
                automation_id=first_webhook.id,
                rotate_webhook_token=True,
            )
        assert conflict_exc.value.error_code == "automation_webhook_token_conflict"
    finally:
        await engine.dispose()


async def test_service_create_webhook_retries_token_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook creation should retry token issuance before surfacing conflict."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        existing = await service.create_webhook(
            profile_id="default",
            name="hook-existing",
            prompt="existing prompt",
        )
        assert existing.webhook is not None
        existing_token = existing.webhook.webhook_token
        assert existing_token is not None
        existing_hash = hash_webhook_token(existing_token)

        calls = {"count": 0}
        original_hash = service_module.hash_webhook_token

        def flaky_hash(token: str) -> str:
            if calls["count"] == 0:
                calls["count"] += 1
                return existing_hash
            return original_hash(token)

        monkeypatch.setattr(service_module, "hash_webhook_token", flaky_hash)

        created = await service.create_webhook(
            profile_id="default",
            name="hook-retried",
            prompt="retried prompt",
        )

        assert calls["count"] == 1
        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        assert created.webhook.webhook_token != existing_token
        assert created.webhook.webhook_path == build_webhook_path(
            "default",
            created.webhook.webhook_token,
        )
        assert created.webhook.webhook_url == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            created.webhook.webhook_token,
        )
    finally:
        await engine.dispose()
