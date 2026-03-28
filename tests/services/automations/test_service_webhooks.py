"""Webhook execution tests for the automation service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.services.automations import AutomationsService
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
        assert webhook.webhook.webhook_path == "/v1/automations/webhook"

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        hook_result = await service.trigger_webhook(
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
        assert "automation_subagent=webhook" in webhook_messages[0]
        assert "subagent_instructions_md:" in webhook_messages[0]
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
        assert "Trigger instructions:" in overrides.prompt_overlay
        assert hook_result.deduplicated is False

        duplicate_result = await service.trigger_webhook(
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
                token=token,
                payload={"event_id": "e-1"},
                agent_loop_factory=factory_fn,
            )

        second_result = await service.trigger_webhook(
            token=token,
            payload={"event_id": "e-1"},
            agent_loop_factory=factory_fn,
        )
        assert second_result.deduplicated is False
        assert len(flaky_loop.calls) == 2

        third_result = await service.trigger_webhook(
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
                token=token,
                payload={"event_id": "same"},
                agent_loop_factory=factory_fn,
            ),
            service_b.trigger_webhook(
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
            token=token,
            payload={"event_id": "evt-a", "body": "same"},
            agent_loop_factory=factory_fn,
        )
        second = await service.trigger_webhook(
            token=token,
            payload={"event_id": "evt-b", "body": "same"},
            agent_loop_factory=factory_fn,
        )
        replay = await service.trigger_webhook(
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


async def test_webhook_trigger_can_carry_explicit_delivery_target(tmp_path: Path) -> None:
    """Webhook execution should keep delivery target in trusted metadata without changing execution target."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="delivery-aware",
            prompt="reply back",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        result = await service.trigger_webhook(
            token=token,
            payload={"event_id": "delivery-1", "body": "hello"},
            delivery_target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="42",
                thread_id="9001",
            ),
            agent_loop_factory=factory_fn,
        )

        assert result.profile_id == "default"
        assert result.session_id.startswith(f"automation-webhook-{created.id}-")
        matching_call = next(call for call in fake_loop.calls if call["session_id"] == result.session_id)
        overrides = matching_call["context_overrides"]
        assert overrides is not None
        assert overrides.runtime_metadata is not None
        assert overrides.runtime_metadata["delivery_target"] == {
            "transport": "telegram",
            "peer_id": "42",
            "thread_id": "9001",
        }
        assert matching_call["profile_id"] == "default"
        assert matching_call["session_id"] == result.session_id
    finally:
        await engine.dispose()


async def test_webhook_trigger_best_effort_delivers_finalize_message(tmp_path: Path) -> None:
    """Webhook automation should best-effort deliver finalized text to explicit delivery target."""

    engine, factory, _ = await prepare_service(tmp_path)

    class _FinalizingLoop:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run_turn(
            self,
            *,
            profile_id: str,
            session_id: str,
            message: str,
            context_overrides: object = None,
        ) -> TurnResult:
            self.calls.append(
                {
                    "profile_id": profile_id,
                    "session_id": session_id,
                    "message": message,
                    "context_overrides": context_overrides,
                }
            )
            return TurnResult(
                run_id=99,
                profile_id=profile_id,
                session_id=session_id,
                envelope=ActionEnvelope(action="finalize", message="delivered final text"),
            )

    class _FakeDeliveryService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def deliver_turn_result(self, *, turn_result: object, target: ChannelDeliveryTarget) -> object:
            self.calls.append({"turn_result": turn_result, "target": target})
            return {"ok": True}

    delivery_service = _FakeDeliveryService()
    service = AutomationsService(
        factory,
        settings=Settings(root_dir=tmp_path),
        channel_delivery_service=delivery_service,  # type: ignore[arg-type]
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="delivery-run",
            prompt="deliver me",
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        finalizing_loop = _FinalizingLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> _FinalizingLoop:
            _ = session, profile_id
            return finalizing_loop

        await service.trigger_webhook(
            token=token,
            payload={"event_id": "delivery-2"},
            delivery_target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="42",
            ),
            agent_loop_factory=factory_fn,
        )

        assert len(delivery_service.calls) == 1
        assert delivery_service.calls[0]["target"].model_dump(exclude_none=True) == {
            "transport": "telegram",
            "peer_id": "42",
        }
        delivered_turn = delivery_service.calls[0]["turn_result"]
        assert isinstance(delivered_turn, TurnResult)
        assert delivered_turn.envelope.message == "delivered final text"
    finally:
        await engine.dispose()


async def test_webhook_trigger_uses_persisted_delivery_target_by_default(tmp_path: Path) -> None:
    """Stored webhook delivery target should flow into runtime metadata and best-effort delivery."""

    engine, factory, _ = await prepare_service(tmp_path)

    class _FinalizingLoop:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run_turn(
            self,
            *,
            profile_id: str,
            session_id: str,
            message: str,
            context_overrides: object = None,
        ) -> TurnResult:
            self.calls.append(
                {
                    "profile_id": profile_id,
                    "session_id": session_id,
                    "message": message,
                    "context_overrides": context_overrides,
                }
            )
            return TurnResult(
                run_id=100,
                profile_id=profile_id,
                session_id=session_id,
                envelope=ActionEnvelope(action="finalize", message="persisted final text"),
            )

    class _FakeDeliveryService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def deliver_turn_result(self, *, turn_result: object, target: ChannelDeliveryTarget) -> object:
            self.calls.append({"turn_result": turn_result, "target": target})
            return {"ok": True}

    delivery_service = _FakeDeliveryService()
    service = AutomationsService(
        factory,
        settings=Settings(root_dir=tmp_path),
        channel_delivery_service=delivery_service,  # type: ignore[arg-type]
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="stored-delivery-run",
            prompt="deliver with stored target",
            delivery_target=ChannelDeliveryTarget(
                transport="smtp",
                address="ops@example.com",
                subject="Stored result",
            ),
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        finalizing_loop = _FinalizingLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> _FinalizingLoop:
            _ = session, profile_id
            return finalizing_loop

        result = await service.trigger_webhook(
            token=token,
            payload={"event_id": "delivery-stored"},
            agent_loop_factory=factory_fn,
        )

        matching_call = next(call for call in finalizing_loop.calls if call["session_id"] == result.session_id)
        overrides = matching_call["context_overrides"]
        assert overrides is not None
        assert overrides.runtime_metadata is not None
        assert overrides.runtime_metadata["delivery_target"] == {
            "transport": "smtp",
            "address": "ops@example.com",
            "subject": "Stored result",
        }
        assert len(delivery_service.calls) == 1
        assert delivery_service.calls[0]["target"].model_dump(exclude_none=True) == {
            "transport": "smtp",
            "address": "ops@example.com",
            "subject": "Stored result",
        }
    finally:
        await engine.dispose()


async def test_webhook_trigger_explicit_delivery_target_overrides_persisted_default(
    tmp_path: Path,
) -> None:
    """Explicit webhook delivery target should override stored delivery target."""

    engine, factory, _ = await prepare_service(tmp_path)

    class _FinalizingLoop:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run_turn(
            self,
            *,
            profile_id: str,
            session_id: str,
            message: str,
            context_overrides: object = None,
        ) -> TurnResult:
            self.calls.append(
                {
                    "profile_id": profile_id,
                    "session_id": session_id,
                    "message": message,
                    "context_overrides": context_overrides,
                }
            )
            return TurnResult(
                run_id=101,
                profile_id=profile_id,
                session_id=session_id,
                envelope=ActionEnvelope(action="finalize", message="explicit final text"),
            )

    class _FakeDeliveryService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def deliver_turn_result(self, *, turn_result: object, target: ChannelDeliveryTarget) -> object:
            self.calls.append({"turn_result": turn_result, "target": target})
            return {"ok": True}

    delivery_service = _FakeDeliveryService()
    service = AutomationsService(
        factory,
        settings=Settings(root_dir=tmp_path),
        channel_delivery_service=delivery_service,  # type: ignore[arg-type]
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="override-delivery-run",
            prompt="deliver with override target",
            delivery_target=ChannelDeliveryTarget(
                transport="smtp",
                address="stored@example.com",
                subject="Stored result",
            ),
        )
        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        finalizing_loop = _FinalizingLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> _FinalizingLoop:
            _ = session, profile_id
            return finalizing_loop

        result = await service.trigger_webhook(
            token=token,
            payload={"event_id": "delivery-explicit"},
            delivery_target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="77",
            ),
            agent_loop_factory=factory_fn,
        )

        matching_call = next(call for call in finalizing_loop.calls if call["session_id"] == result.session_id)
        overrides = matching_call["context_overrides"]
        assert overrides is not None
        assert overrides.runtime_metadata is not None
        assert overrides.runtime_metadata["delivery_target"] == {
            "transport": "telegram",
            "peer_id": "77",
        }
        assert len(delivery_service.calls) == 1
        assert delivery_service.calls[0]["target"].model_dump(exclude_none=True) == {
            "transport": "telegram",
            "peer_id": "77",
        }
    finally:
        await engine.dispose()
