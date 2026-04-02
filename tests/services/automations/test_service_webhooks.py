"""Webhook execution tests for the automation service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.services.automations import AutomationsService
from afkbot.services.automations.webhook_tokens import build_webhook_path, build_webhook_url
from afkbot.settings import Settings
from tests.services.automations._harness import (
    BlockingLoop,
    FailingOnceLoop,
    FakeLoop,
    prepare_service,
)


async def test_service_trigger_webhook_sanitizes_payload_and_deduplicates(tmp_path: Path) -> None:
    """Webhook trigger should compose one sanitized message and deduplicate retries."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        webhook = await service.create_webhook(
            profile_id="default",
            name="incoming",
            prompt="process incoming",
        )
        token = webhook.webhook.webhook_token if webhook.webhook is not None else ""
        assert token
        assert webhook.webhook is not None
        assert isinstance(webhook.webhook.webhook_token_masked, str)
        assert webhook.webhook.webhook_path == build_webhook_path("default", token)
        assert webhook.webhook.webhook_url == build_webhook_url(
            "http://127.0.0.1:8080",
            "default",
            token,
        )
        assert webhook.webhook.last_execution_status == "idle"
        assert webhook.webhook.last_session_id is None
        assert webhook.webhook.last_started_at is None
        assert webhook.webhook.last_succeeded_at is None
        assert webhook.webhook.last_failed_at is None
        assert webhook.webhook.last_error is None
        assert webhook.webhook.last_event_hash is None
        assert webhook.webhook.chat_resume_command is None

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        hook_result = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-1", "k": "v", "api_token": "short"},
            agent_loop_factory=factory_fn,
        )
        assert hook_result.automation_id == webhook.id
        assert hook_result.session_id.startswith(f"automation-webhook-{webhook.id}-")
        assert hook_result.payload["api_token"] == "[REDACTED]"
        webhook_messages = [
            call["message"]
            for call in fake_loop.calls
            if call["session_id"] == hook_result.session_id
        ]
        assert len(webhook_messages) == 1
        assert webhook_messages[0].startswith("process incoming\n\nwebhook_payload=")
        assert '"api_token": "[REDACTED]"' in webhook_messages[0]
        assert '"api_token": "short"' not in webhook_messages[0]

        matching_call = next(call for call in fake_loop.calls if call["session_id"] == hook_result.session_id)
        overrides = matching_call["context_overrides"]
        assert overrides is not None
        assert overrides.runtime_metadata is not None
        assert overrides.runtime_metadata["transport"] == "automation"
        assert overrides.runtime_metadata["account_id"] == str(webhook.id)
        assert overrides.runtime_metadata["automation"]["automation_id"] == webhook.id
        assert overrides.runtime_metadata["automation"]["trigger_type"] == "webhook"
        assert overrides.runtime_metadata["automation"]["payload_keys"] == (
            "api_token",
            "event_id",
            "k",
        )
        assert isinstance(overrides.runtime_metadata["automation"]["event_hash"], str)
        assert overrides.runtime_metadata["automation"]["event_hash"]
        assert overrides.prompt_overlay is not None
        assert "Automation execution context." in overrides.prompt_overlay
        assert hook_result.deduplicated is False
        metadata_after_success = await service.get(profile_id="default", automation_id=webhook.id)
        assert metadata_after_success.webhook is not None
        assert metadata_after_success.webhook.last_execution_status == "succeeded"
        assert metadata_after_success.webhook.last_session_id == hook_result.session_id
        assert metadata_after_success.webhook.last_started_at is not None
        assert metadata_after_success.webhook.last_succeeded_at is not None
        assert metadata_after_success.webhook.last_failed_at is None
        assert metadata_after_success.webhook.last_error is None
        assert metadata_after_success.webhook.last_event_hash is not None
        assert (
            metadata_after_success.webhook.chat_resume_command
            == f"afk chat --profile default --session {hook_result.session_id}"
        )

        duplicate_result = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-1", "k": "v", "api_token": "short"},
            agent_loop_factory=factory_fn,
        )
        assert duplicate_result.deduplicated is True
        webhook_messages_after_duplicate = [
            call["message"]
            for call in fake_loop.calls
            if call["session_id"] == hook_result.session_id
        ]
        assert len(webhook_messages_after_duplicate) == 1
    finally:
        await engine.dispose()


async def test_service_webhook_metadata_prefers_public_runtime_url(tmp_path: Path) -> None:
    """Webhook metadata should use the configured public runtime URL when available."""

    engine, factory, _ = await prepare_service(tmp_path)
    service = AutomationsService(
        factory,
        settings=Settings(
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
            root_dir=tmp_path,
            public_runtime_url="https://hooks.example.com/base",
        ),
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="public hook",
            prompt="handle public events",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None
        assert created.webhook.webhook_url == build_webhook_url(
            "https://hooks.example.com/base",
            "default",
            token,
        )
    finally:
        await engine.dispose()


async def test_webhook_claim_persists_when_run_fails(tmp_path: Path) -> None:
    """Failed webhook execution should be retryable, then deduplicated after success."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="unstable hook",
            prompt="handle event",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        flaky_loop = FailingOnceLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FailingOnceLoop:
            _ = session, profile_id
            return flaky_loop

        with pytest.raises(RuntimeError, match="simulated failure"):
            await service.trigger_webhook(
                profile_id="default",
                token=token,
                payload={"event_id": "e-1"},
                agent_loop_factory=factory_fn,
            )
        metadata_after_failure = await service.get(profile_id="default", automation_id=created.id)
        assert metadata_after_failure.webhook is not None
        assert metadata_after_failure.webhook.last_execution_status == "failed"
        assert metadata_after_failure.webhook.last_started_at is not None
        assert metadata_after_failure.webhook.last_failed_at is not None
        assert metadata_after_failure.webhook.last_succeeded_at is None
        assert metadata_after_failure.webhook.last_error == "RuntimeError: simulated failure after side-effect"
        assert metadata_after_failure.webhook.last_session_id is not None

        second_result = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "e-1"},
            agent_loop_factory=factory_fn,
        )
        assert second_result.deduplicated is False
        assert len(flaky_loop.calls) == 2
        metadata_after_retry = await service.get(profile_id="default", automation_id=created.id)
        assert metadata_after_retry.webhook is not None
        assert metadata_after_retry.webhook.last_execution_status == "succeeded"
        assert metadata_after_retry.webhook.last_started_at is not None
        assert metadata_after_retry.webhook.last_succeeded_at is not None
        assert metadata_after_retry.webhook.last_error is None
        assert metadata_after_retry.webhook.last_session_id == second_result.session_id

        third_result = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "e-1"},
            agent_loop_factory=factory_fn,
        )
        assert third_result.deduplicated is True
        assert len(flaky_loop.calls) == 2
    finally:
        await engine.dispose()


async def test_webhook_claim_released_on_cancellation(tmp_path: Path) -> None:
    """Cancellation should release webhook claim so next event executes immediately."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="cancel hook",
            prompt="handle cancel",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        blocking_loop = BlockingLoop()

        def blocking_factory(session: AsyncSession, profile_id: str) -> BlockingLoop:
            _ = session, profile_id
            return blocking_loop

        task = asyncio.create_task(
            service.trigger_webhook(
                profile_id="default",
                token=token,
                payload={"event_id": "cancel-1"},
                agent_loop_factory=blocking_factory,
            )
        )
        await asyncio.wait_for(blocking_loop.started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        followup_loop = FakeLoop()

        def followup_factory(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return followup_loop

        followup_result = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "cancel-2"},
            agent_loop_factory=followup_factory,
        )
        assert followup_result.deduplicated is False
        assert len(followup_loop.calls) == 1
    finally:
        await engine.dispose()


async def test_webhook_concurrency_deduplicates_across_service_instances(tmp_path: Path) -> None:
    """Parallel webhook calls from two service instances must execute only once."""

    engine, factory, service_a = await prepare_service(tmp_path)
    service_b = AutomationsService(factory, settings=Settings(root_dir=tmp_path))
    try:
        created = await service_a.create_webhook(
            profile_id="default",
            name="parallel hook",
            prompt="process one",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        res_a, res_b = await asyncio.gather(
            service_a.trigger_webhook(
                profile_id="default",
                token=token,
                payload={"event_id": "same"},
                agent_loop_factory=factory_fn,
            ),
            service_b.trigger_webhook(
                profile_id="default",
                token=token,
                payload={"event_id": "same"},
                agent_loop_factory=factory_fn,
            ),
        )
        assert sorted([res_a.deduplicated, res_b.deduplicated]) == [False, True]
        webhook_sessions = [call["session_id"] for call in fake_loop.calls]
        assert (
            sum(
                1
                for session_id in webhook_sessions
                if session_id.startswith(f"automation-webhook-{created.id}-")
            )
            == 1
        )
    finally:
        await engine.dispose()


async def test_webhook_same_body_different_event_id_executes_twice(tmp_path: Path) -> None:
    """Different delivery ids with identical body must both execute."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="event-key-hook",
            prompt="handle delivery",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        first = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-a", "body": "same"},
            agent_loop_factory=factory_fn,
        )
        second = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-b", "body": "same"},
            agent_loop_factory=factory_fn,
        )
        replay = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-a", "body": "same"},
            agent_loop_factory=factory_fn,
        )
        assert first.deduplicated is False
        assert second.deduplicated is False
        assert replay.deduplicated is True
        assert first.session_id != second.session_id
        assert replay.session_id == first.session_id
        assert len(fake_loop.calls) == 2
    finally:
        await engine.dispose()
