"""Cron execution tests for the automation service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

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
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(seconds=1),
            )
            due_fallback, _ = await repo.create_cron_automation(
                profile_id="default",
                name="due-fallback",
                prompt="fallback",
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
        assert sorted(cron_messages) == ["fallback", "star"]

        cron_overrides = [
            call["context_overrides"]
            for call in fake_loop.calls
            if call["session_id"].startswith("automation-cron-")
        ]
        assert len(cron_overrides) == 2
        for overrides in cron_overrides:
            assert overrides is not None
            assert overrides.runtime_metadata is not None
            assert overrides.runtime_metadata["transport"] == "automation"
            assert overrides.runtime_metadata["automation"]["trigger_type"] == "cron"
            assert "automation_id" in overrides.runtime_metadata["automation"]
            assert "cron_expr" in overrides.runtime_metadata["automation"]
            assert overrides.prompt_overlay is not None
            assert "Automation execution context." in overrides.prompt_overlay

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
