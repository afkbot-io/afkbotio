"""Tests for AutomationRepository CRUD and trigger queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.webhook_tokens import stored_webhook_token_ref
from tests.repositories._harness import build_repository_factory


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    return await build_repository_factory(
        tmp_path,
        db_name="automation_repo.db",
        profile_ids=("default", "other"),
    )


async def test_repository_create_list_get_delete(tmp_path: Path) -> None:
    """Repository should support CRUD and profile isolation with soft delete."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            created_cron, cron = await repo.create_cron_automation(
                profile_id="default",
                name="each minute",
                prompt="run cron task",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=datetime.now(timezone.utc),
            )
            webhook_hash = sha256("tok_abc".encode("utf-8")).hexdigest()
            created_webhook, webhook = await repo.create_webhook_automation(
                profile_id="other",
                name="incoming webhook",
                prompt="handle webhook",
                webhook_token_hash=webhook_hash,
            )

            fetched = await repo.get_by_id(profile_id="default", automation_id=created_cron.id)
            assert fetched is not None
            automation, fetched_cron, fetched_webhook = fetched
            assert automation.id == created_cron.id
            assert fetched_cron is not None
            assert fetched_cron.cron_expr == "* * * * *"
            assert fetched_webhook is None

            listed_default = await repo.list_by_profile(profile_id="default")
            assert len(listed_default) == 1
            assert listed_default[0][0].id == created_cron.id

            listed_other = await repo.list_by_profile(profile_id="other")
            assert len(listed_other) == 1
            assert listed_other[0][0].id == created_webhook.id
            assert listed_other[0][2] is not None
            assert listed_other[0][2].webhook_token == stored_webhook_token_ref(webhook_hash)
            assert listed_other[0][2].webhook_token_hash == webhook.webhook_token_hash

            assert (
                await repo.soft_delete(profile_id="default", automation_id=created_cron.id) is True
            )
            assert (
                await repo.soft_delete(profile_id="default", automation_id=created_cron.id) is False
            )

            listed_no_deleted = await repo.list_by_profile(profile_id="default")
            assert listed_no_deleted == []

            listed_with_deleted = await repo.list_by_profile(
                profile_id="default",
                include_deleted=True,
            )
            assert len(listed_with_deleted) == 1
            assert listed_with_deleted[0][0].status == "deleted"
            assert cron.automation_id == created_cron.id
    finally:
        await engine.dispose()


async def test_repository_webhook_lookup_and_due_cron(tmp_path: Path) -> None:
    """Repository should resolve webhook by token and due cron rows."""

    engine, factory = await _prepare(tmp_path)
    now = datetime.now(timezone.utc)
    try:
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            due_automation, _ = await repo.create_cron_automation(
                profile_id="default",
                name="due",
                prompt="due prompt",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(minutes=1),
            )
            fallback_automation, _ = await repo.create_cron_automation(
                profile_id="default",
                name="fallback",
                prompt="fallback prompt",
                cron_expr="0 * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(minutes=1),
            )
            future_automation, _ = await repo.create_cron_automation(
                profile_id="default",
                name="future",
                prompt="future prompt",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now + timedelta(hours=1),
            )
            deleted_cron, _ = await repo.create_cron_automation(
                profile_id="default",
                name="deleted-cron",
                prompt="deleted cron",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now - timedelta(minutes=1),
            )
            webhook_automation, _ = await repo.create_webhook_automation(
                profile_id="default",
                name="hook",
                prompt="hook prompt",
                webhook_token_hash=sha256("tok_lookup".encode("utf-8")).hexdigest(),
            )
            deleted_webhook, _ = await repo.create_webhook_automation(
                profile_id="default",
                name="deleted-hook",
                prompt="deleted hook",
                webhook_token_hash=sha256("tok_deleted".encode("utf-8")).hexdigest(),
            )

            webhook_row = await repo.find_webhook_by_target(
                profile_id="default", token="tok_lookup"
            )
            assert webhook_row is not None
            assert webhook_row[0].id == webhook_automation.id
            assert await repo.find_webhook_by_target(profile_id="default", token="missing") is None
            first_mark = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now,
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-1",
                session_id="session-1",
            )
            started = await repo.mark_webhook_started(
                automation_id=webhook_automation.id,
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                claim_token="w-1",
                started_at=now + timedelta(seconds=1),
            )
            duplicate_mark = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now + timedelta(seconds=5),
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-2",
                session_id="session-2",
            )
            parallel_other_mark = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now + timedelta(seconds=5),
                event_hash=sha256("event-2".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-3",
                session_id="session-3",
            )
            wrong_complete = await repo.complete_webhook_event(
                automation_id=webhook_automation.id,
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                claim_token="wrong-token",
                completed_at=now + timedelta(seconds=5),
            )
            released = await repo.release_webhook_event(
                automation_id=webhook_automation.id,
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                claim_token="w-1",
                failed_at=now + timedelta(seconds=5),
                error_message="RuntimeError: webhook failed",
            )
            retried_mark = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now + timedelta(seconds=6),
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-4",
                session_id="session-4",
            )
            completed = await repo.complete_webhook_event(
                automation_id=webhook_automation.id,
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                claim_token="w-4",
                completed_at=now + timedelta(seconds=7),
            )
            second_mark = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now + timedelta(seconds=10),
                event_hash=sha256("event-2".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-5",
                session_id="session-5",
            )
            completed_second = await repo.complete_webhook_event(
                automation_id=webhook_automation.id,
                event_hash=sha256("event-2".encode("utf-8")).hexdigest(),
                claim_token="w-5",
                completed_at=now + timedelta(seconds=11),
            )
            replay_first = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now + timedelta(seconds=20),
                event_hash=sha256("event-1".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-6",
                session_id="session-6",
            )
            assert first_mark is True
            assert started is True
            assert duplicate_mark is False
            assert parallel_other_mark is False
            assert wrong_complete is False
            assert released is True
            assert retried_mark is True
            assert completed is True
            assert second_mark is True
            assert completed_second is True
            assert replay_first is False
            webhook_row_after = await repo.get_by_id(
                profile_id="default",
                automation_id=webhook_automation.id,
            )
            assert webhook_row_after is not None
            assert webhook_row_after[2] is not None
            assert webhook_row_after[2].last_session_id == "session-5"
            assert webhook_row_after[2].last_error is None
            assert webhook_row_after[2].last_started_at == (now + timedelta(seconds=1)).replace(
                tzinfo=None
            )
            assert webhook_row_after[2].last_failed_at == (now + timedelta(seconds=5)).replace(
                tzinfo=None
            )
            assert webhook_row_after[2].last_succeeded_at == (now + timedelta(seconds=11)).replace(
                tzinfo=None
            )

            due_rows = await repo.list_due_cron(now_utc=now)
            due_ids = {row[0].id for row in due_rows}
            assert due_ids == {due_automation.id, fallback_automation.id, deleted_cron.id}
            assert future_automation.id not in due_ids

            next_run_at = now + timedelta(minutes=1)
            claimed = await repo.claim_cron_execution(
                automation_id=due_automation.id,
                due_before_or_at=now,
                claim_until=now + timedelta(minutes=15),
                claim_token="c-1",
            )
            duplicate_claim = await repo.claim_cron_execution(
                automation_id=due_automation.id,
                due_before_or_at=now,
                claim_until=now + timedelta(minutes=15),
                claim_token="c-2",
            )
            released_claim = await repo.release_cron_claim(
                automation_id=due_automation.id,
                claim_token="c-1",
            )
            retried_claim = await repo.claim_cron_execution(
                automation_id=due_automation.id,
                due_before_or_at=now,
                claim_until=now + timedelta(minutes=15),
                claim_token="c-3",
            )
            marked = await repo.mark_cron_executed(
                automation_id=due_automation.id,
                next_run_at=next_run_at,
                executed_at=now,
                claim_token="c-3",
            )
            assert claimed is True
            assert duplicate_claim is False
            assert released_claim is True
            assert retried_claim is True
            assert marked is True

            due_after_mark = await repo.list_due_cron(now_utc=now)
            due_after_ids = {row[0].id for row in due_after_mark}
            assert due_automation.id not in due_after_ids
            assert fallback_automation.id in due_after_ids

            assert await repo.soft_delete(
                profile_id="default",
                automation_id=deleted_cron.id,
            )
            assert await repo.soft_delete(
                profile_id="default",
                automation_id=deleted_webhook.id,
            )
            deleted_cron_claim = await repo.claim_cron_execution(
                automation_id=deleted_cron.id,
                due_before_or_at=now,
                claim_until=now + timedelta(minutes=15),
                claim_token="c-deleted",
            )
            deleted_webhook_claim = await repo.claim_webhook_event(
                automation_id=deleted_webhook.id,
                received_at=now,
                event_hash=sha256("deleted-event".encode("utf-8")).hexdigest(),
                lease_until=now + timedelta(minutes=15),
                claim_token="w-deleted",
                session_id="deleted-session",
            )
            assert deleted_cron_claim is False
            assert deleted_webhook_claim is False
    finally:
        await engine.dispose()


async def test_repository_update_automation_and_trigger_rows(tmp_path: Path) -> None:
    """Repository update methods should change only target mutable fields."""

    engine, factory = await _prepare(tmp_path)
    now = datetime.now(timezone.utc)
    try:
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            cron_automation, _ = await repo.create_cron_automation(
                profile_id="default",
                name="cron-original",
                prompt="cron prompt",
                cron_expr="* * * * *",
                timezone="UTC",
                next_run_at=now + timedelta(minutes=1),
            )
            webhook_automation, _ = await repo.create_webhook_automation(
                profile_id="default",
                name="webhook-original",
                prompt="webhook prompt",
                webhook_token_hash=sha256("tok_old".encode("utf-8")).hexdigest(),
            )

            event_hash = sha256("evt-repo-update".encode("utf-8")).hexdigest()
            lease_until = now + timedelta(minutes=15)
            claimed = await repo.claim_webhook_event(
                automation_id=webhook_automation.id,
                received_at=now,
                event_hash=event_hash,
                lease_until=lease_until,
                claim_token="repo-claim",
                session_id="repo-session",
            )
            assert claimed is True
            started = await repo.mark_webhook_started(
                automation_id=webhook_automation.id,
                event_hash=event_hash,
                claim_token="repo-claim",
                started_at=now + timedelta(seconds=1),
            )
            assert started is True

            updated_automation = await repo.update_automation(
                profile_id="default",
                automation_id=cron_automation.id,
                name="cron-updated",
                prompt="cron updated prompt",
                status="paused",
            )
            assert updated_automation is not None
            assert updated_automation.name == "cron-updated"
            assert updated_automation.prompt == "cron updated prompt"
            assert updated_automation.status == "paused"

            next_run_at = now + timedelta(hours=2)
            updated_cron = await repo.update_cron_trigger(
                automation_id=cron_automation.id,
                cron_expr="0 * * * *",
                timezone_name="Europe/Berlin",
                next_run_at=next_run_at,
            )
            assert updated_cron is not None
            assert updated_cron.cron_expr == "0 * * * *"
            assert updated_cron.timezone == "Europe/Berlin"
            assert updated_cron.next_run_at == next_run_at.replace(tzinfo=None)

            new_token_hash = sha256("tok_new".encode("utf-8")).hexdigest()
            updated_webhook = await repo.update_webhook_trigger(
                automation_id=webhook_automation.id,
                webhook_token_hash=new_token_hash,
            )
            assert updated_webhook is not None
            assert updated_webhook.webhook_token == stored_webhook_token_ref(new_token_hash)
            assert updated_webhook.webhook_token_hash == new_token_hash
            assert updated_webhook.in_progress_event_hash == event_hash
            assert updated_webhook.claim_token == "repo-claim"
            assert updated_webhook.in_progress_until == lease_until.replace(tzinfo=None)
            assert updated_webhook.last_received_at == now.replace(tzinfo=None)
            assert updated_webhook.last_session_id == "repo-session"
            assert updated_webhook.last_started_at == (now + timedelta(seconds=1)).replace(
                tzinfo=None
            )

            updated_before_touch = updated_automation.updated_at
            touched = await repo.touch_automation(
                profile_id="default",
                automation_id=cron_automation.id,
            )
            assert touched is not None
            assert touched.updated_at > updated_before_touch

            wrong_profile_update = await repo.update_automation(
                profile_id="other",
                automation_id=cron_automation.id,
                name="should-not-apply",
            )
            assert wrong_profile_update is None
    finally:
        await engine.dispose()


async def test_repository_list_due_cron_respects_limit(tmp_path: Path) -> None:
    """Due cron query should honor explicit batch limit."""

    engine, factory = await _prepare(tmp_path)
    now = datetime.now(timezone.utc)
    try:
        async with session_scope(factory) as session:
            repo = AutomationRepository(session)
            for index in range(5):
                await repo.create_cron_automation(
                    profile_id="default",
                    name=f"batch-{index}",
                    prompt="due prompt",
                    cron_expr="* * * * *",
                    timezone="UTC",
                    next_run_at=now - timedelta(minutes=1),
                )

            due_rows = await repo.list_due_cron(now_utc=now, limit=2)
            assert len(due_rows) == 2
    finally:
        await engine.dispose()
