"""Cron execution tests for the automation service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.db.session import session_scope
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations import AutomationsService
from afkbot.settings import Settings
from tests.services.automations._harness import FailingOnceLoop, FakeLoop, prepare_service


async def test_service_tick_cron_updates_schedule_fields(tmp_path: Path) -> None:
    """Cron tick should call the loop and persist last/next run timestamps."""

    engine, factory, service = await prepare_service(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            due_star, _ = await repo.create_cron_automation(
                profile_id="default",
                name="due-star",
                prompt="star",
                delivery_mode="tool",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(seconds=1),
            )
            due_fallback, _ = await repo.create_cron_automation(
                profile_id="default",
                name="due-fallback",
                prompt="fallback",
                delivery_mode="tool",
                cron_expr="0 * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(seconds=1),
            )

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        tick_result = await service.tick_cron(now_utc=now, agent_loop_factory=factory_fn)
        assert set(tick_result.triggered_ids) == {due_star.id, due_fallback.id}
        assert tick_result.failed_ids == []

        call_sessions = {call["session_id"] for call in fake_loop.calls}
        assert any(
            session_id.startswith(f"automation-cron-{due_star.id}-")
            for session_id in call_sessions
        )
        assert any(
            session_id.startswith(f"automation-cron-{due_fallback.id}-")
            for session_id in call_sessions
        )
        cron_messages = [
            call["message"]
            for call in fake_loop.calls
            if call["session_id"].startswith("automation-cron-")
        ]
        assert cron_messages
        assert all("automation_subagent=cron" in message for message in cron_messages)
        cron_overrides = [
            call["context_overrides"]
            for call in fake_loop.calls
            if call["session_id"].startswith("automation-cron-")
        ]
        assert len(cron_overrides) == 2
        assert cron_overrides[0] is not None
        assert cron_overrides[1] is not None
        assert cron_overrides[0].runtime_metadata == {
            "transport": "automation",
            "account_id": str(due_star.id),
            "automation": {
                "automation_id": due_star.id,
                "trigger_type": "cron",
                "cron_expr": "* * * * *",
                "delivery_mode": "tool",
            }
        }
        assert cron_overrides[1].runtime_metadata == {
            "transport": "automation",
            "account_id": str(due_fallback.id),
            "automation": {
                "automation_id": due_fallback.id,
                "trigger_type": "cron",
                "cron_expr": "0 * * * *",
                "delivery_mode": "tool",
            }
        }
        assert "Trigger instructions:" in str(cron_overrides[0].prompt_overlay)
        assert "Trigger instructions:" in str(cron_overrides[1].prompt_overlay)

        async with session_scope(factory) as session:
            star = await session.get(AutomationTriggerCron, due_star.id)
            fallback = await session.get(AutomationTriggerCron, due_fallback.id)
            assert star is not None
            assert fallback is not None
            expected_now = now.replace(tzinfo=None)
            assert star.last_run_at == expected_now
            assert fallback.last_run_at == expected_now
            expected_star_next = (
                now.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
            )
            expected_fallback_next = (
                now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                + timedelta(hours=1)
            )
            assert star.next_run_at == expected_star_next.replace(tzinfo=None)
            assert fallback.next_run_at == expected_fallback_next.replace(tzinfo=None)
    finally:
        await engine.dispose()


async def test_cron_concurrency_claims_once_across_service_instances(tmp_path: Path) -> None:
    """Parallel cron ticks from two service instances should run one due job once."""

    engine, factory, service_a = await prepare_service(tmp_path)
    service_b = AutomationsService(factory, settings=Settings(root_dir=tmp_path))
    try:
        now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            due, _ = await repo.create_cron_automation(
                profile_id="default",
                name="parallel-cron",
                prompt="execute once",
                delivery_mode="tool",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(seconds=1),
            )

        fake_loop = FakeLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FakeLoop:
            _ = session, profile_id
            return fake_loop

        tick_a, tick_b = await asyncio.gather(
            service_a.tick_cron(now_utc=now, agent_loop_factory=factory_fn),
            service_b.tick_cron(now_utc=now, agent_loop_factory=factory_fn),
        )
        all_triggered = tick_a.triggered_ids + tick_b.triggered_ids
        assert all_triggered.count(due.id) == 1
        cron_sessions = [call["session_id"] for call in fake_loop.calls]
        assert (
            sum(
                1
                for session_id in cron_sessions
                if session_id.startswith(f"automation-cron-{due.id}-")
            )
            == 1
        )
    finally:
        await engine.dispose()


async def test_cron_failed_execution_is_retryable(tmp_path: Path) -> None:
    """Cron task should be claim-released on failure and retried on next tick."""

    engine, factory, service = await prepare_service(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            due, _ = await repo.create_cron_automation(
                profile_id="default",
                name="retry-cron",
                prompt="retry me",
                delivery_mode="tool",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(seconds=1),
            )

        flaky_loop = FailingOnceLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> FailingOnceLoop:
            _ = session, profile_id
            return flaky_loop

        first = await service.tick_cron(now_utc=now, agent_loop_factory=factory_fn)
        assert first.triggered_ids == []
        assert first.failed_ids == [due.id]

        second = await service.tick_cron(now_utc=now, agent_loop_factory=factory_fn)
        assert second.triggered_ids == [due.id]
        assert second.failed_ids == []
        assert len(flaky_loop.calls) == 2
    finally:
        await engine.dispose()


async def test_service_tick_cron_delivers_finalize_message_to_persisted_target(
    tmp_path: Path,
) -> None:
    """Cron execution should reuse stored delivery target for runtime metadata and best-effort delivery."""

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
                run_id=88,
                profile_id=profile_id,
                session_id=session_id,
                envelope=ActionEnvelope(action="finalize", message="cron delivery text"),
            )

    class _FakeDeliveryService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def deliver_turn_result(self, *, turn_result: object, target: ChannelDeliveryTarget) -> object:
            self.calls.append({"turn_result": turn_result, "target": target})
            return {"ok": True}

    fake_delivery_service = _FakeDeliveryService()
    service = AutomationsService(
        factory,
        settings=Settings(root_dir=tmp_path),
        channel_delivery_service=fake_delivery_service,  # type: ignore[arg-type]
    )
    try:
        now = datetime.now(timezone.utc)
        created = await service.create_cron(
            profile_id="default",
            name="cron-delivery",
            prompt="deliver cron result",
            cron_expr="* * * * *",
            timezone_name="UTC",
            delivery_target=ChannelDeliveryTarget(
                transport="smtp",
                address="ops@example.com",
                subject="Cron result",
            ),
        )
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            updated = await repo.update_cron_trigger(
                automation_id=created.id,
                next_run_at=now - timedelta(seconds=1),
            )
            assert updated is not None

        finalizing_loop = _FinalizingLoop()

        def factory_fn(session: AsyncSession, profile_id: str) -> _FinalizingLoop:
            _ = session, profile_id
            return finalizing_loop

        tick_result = await service.tick_cron(now_utc=now, agent_loop_factory=factory_fn)

        assert tick_result.triggered_ids == [created.id]
        assert tick_result.failed_ids == []
        assert len(finalizing_loop.calls) == 1
        overrides = finalizing_loop.calls[0]["context_overrides"]
        assert overrides is not None
        assert overrides.runtime_metadata is not None
        assert overrides.runtime_metadata["delivery_target"] == {
            "transport": "smtp",
            "address": "ops@example.com",
            "subject": "Cron result",
        }
        assert len(fake_delivery_service.calls) == 1
        assert fake_delivery_service.calls[0]["target"].model_dump(exclude_none=True) == {
            "transport": "smtp",
            "address": "ops@example.com",
            "subject": "Cron result",
        }
    finally:
        await engine.dispose()
