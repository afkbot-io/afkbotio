"""Claim and lease management mixin for automation repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.automation import Automation
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook
from afkbot.models.automation_webhook_processed_event import AutomationWebhookProcessedEvent
from afkbot.repositories.automation_repo_common import result_succeeded


class AutomationRepositoryClaimsMixin:
    """Cron/webhook claim and completion operations."""

    _session: AsyncSession

    async def mark_cron_executed(
        self,
        *,
        automation_id: int,
        next_run_at: datetime,
        executed_at: datetime,
        claim_token: str,
    ) -> bool:
        """Update cron execution timestamps for one automation id."""

        statement = (
            update(AutomationTriggerCron)
            .where(
                AutomationTriggerCron.automation_id == automation_id,
                AutomationTriggerCron.claim_token == claim_token,
            )
            .values(
                last_run_at=executed_at,
                next_run_at=next_run_at,
                claim_token=None,
                claimed_until=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def claim_cron_execution(
        self,
        *,
        automation_id: int,
        due_before_or_at: datetime,
        claim_until: datetime,
        claim_token: str,
    ) -> bool:
        """Atomically claim due cron execution; True only for one winner."""

        active_automation_subquery = (
            select(Automation.id)
            .where(
                Automation.id == automation_id,
                Automation.status == "active",
                Automation.trigger_type == "cron",
            )
            .exists()
        )
        statement = (
            update(AutomationTriggerCron)
            .where(
                AutomationTriggerCron.automation_id == automation_id,
                active_automation_subquery,
                or_(
                    AutomationTriggerCron.next_run_at.is_(None),
                    AutomationTriggerCron.next_run_at <= due_before_or_at,
                ),
                or_(
                    AutomationTriggerCron.claimed_until.is_(None),
                    AutomationTriggerCron.claimed_until <= due_before_or_at,
                ),
            )
            .values(claimed_until=claim_until, claim_token=claim_token)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def refresh_cron_claim(
        self,
        *,
        automation_id: int,
        claim_token: str,
        claim_until: datetime,
    ) -> bool:
        """Extend one claimed cron lease while the owner is still running."""

        statement = (
            update(AutomationTriggerCron)
            .where(
                AutomationTriggerCron.automation_id == automation_id,
                AutomationTriggerCron.claim_token == claim_token,
            )
            .values(claimed_until=claim_until)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def release_cron_claim(self, *, automation_id: int, claim_token: str) -> bool:
        """Release claimed cron row so it can be retried."""

        statement = (
            update(AutomationTriggerCron)
            .where(
                AutomationTriggerCron.automation_id == automation_id,
                AutomationTriggerCron.claim_token == claim_token,
            )
            .values(claimed_until=None, claim_token=None)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def claim_webhook_event(
        self,
        *,
        automation_id: int,
        received_at: datetime,
        event_hash: str,
        lease_until: datetime,
        claim_token: str,
        session_id: str,
    ) -> bool:
        """Atomically claim webhook event; False when duplicate or trigger missing."""

        processed_exists = (
            await self._session.get(
                AutomationWebhookProcessedEvent,
                {"automation_id": automation_id, "event_hash": event_hash},
            )
            is not None
        )
        if processed_exists:
            return False

        processed_subquery = (
            select(AutomationWebhookProcessedEvent.automation_id)
            .where(
                AutomationWebhookProcessedEvent.automation_id == automation_id,
                AutomationWebhookProcessedEvent.event_hash == event_hash,
            )
            .exists()
        )
        active_automation_subquery = (
            select(Automation.id)
            .where(
                Automation.id == automation_id,
                Automation.status == "active",
                Automation.trigger_type == "webhook",
            )
            .exists()
        )
        statement = (
            update(AutomationTriggerWebhook)
            .where(
                AutomationTriggerWebhook.automation_id == automation_id,
                active_automation_subquery,
                ~processed_subquery,
                or_(
                    AutomationTriggerWebhook.in_progress_event_hash.is_(None),
                    AutomationTriggerWebhook.in_progress_until.is_(None),
                    AutomationTriggerWebhook.in_progress_until <= received_at,
                ),
            )
            .values(
                in_progress_event_hash=event_hash,
                claim_token=claim_token,
                in_progress_until=lease_until,
                last_received_at=received_at,
                last_session_id=session_id,
                last_error=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def complete_webhook_event(
        self,
        *,
        automation_id: int,
        event_hash: str,
        claim_token: str,
        completed_at: datetime,
    ) -> bool:
        """Mark currently claimed webhook event as completed."""

        statement = (
            update(AutomationTriggerWebhook)
            .where(
                AutomationTriggerWebhook.automation_id == automation_id,
                AutomationTriggerWebhook.in_progress_event_hash == event_hash,
                AutomationTriggerWebhook.claim_token == claim_token,
            )
            .values(
                last_event_hash=event_hash,
                in_progress_event_hash=None,
                claim_token=None,
                in_progress_until=None,
                last_succeeded_at=completed_at,
                last_error=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        if not result_succeeded(result):
            await self._session.flush()
            return False

        await self._record_processed_webhook_event(
            automation_id=automation_id,
            event_hash=event_hash,
        )
        await self._session.flush()
        return True

    async def release_webhook_event(
        self,
        *,
        automation_id: int,
        event_hash: str,
        claim_token: str,
        failed_at: datetime,
        error_message: str,
    ) -> bool:
        """Release currently claimed webhook event after execution failure."""

        statement = (
            update(AutomationTriggerWebhook)
            .where(
                AutomationTriggerWebhook.automation_id == automation_id,
                AutomationTriggerWebhook.in_progress_event_hash == event_hash,
                AutomationTriggerWebhook.claim_token == claim_token,
            )
            .values(
                in_progress_event_hash=None,
                claim_token=None,
                in_progress_until=None,
                last_failed_at=failed_at,
                last_error=error_message,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def mark_webhook_started(
        self,
        *,
        automation_id: int,
        event_hash: str,
        claim_token: str,
        started_at: datetime,
    ) -> bool:
        """Persist that a claimed webhook moved from received to active execution."""

        statement = (
            update(AutomationTriggerWebhook)
            .where(
                AutomationTriggerWebhook.automation_id == automation_id,
                AutomationTriggerWebhook.in_progress_event_hash == event_hash,
                AutomationTriggerWebhook.claim_token == claim_token,
            )
            .values(last_started_at=started_at)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)

    async def _record_processed_webhook_event(
        self,
        *,
        automation_id: int,
        event_hash: str,
    ) -> None:
        """Best-effort insert of the processed-event marker for webhook idempotency."""

        processed_row = AutomationWebhookProcessedEvent(
            automation_id=automation_id,
            event_hash=event_hash,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(processed_row)
                await self._session.flush()
        except IntegrityError:
            # Another worker won the race to persist the same processed marker.
            return

    async def refresh_webhook_claim(
        self,
        *,
        automation_id: int,
        event_hash: str,
        claim_token: str,
        lease_until: datetime,
    ) -> bool:
        """Extend one claimed webhook lease while owner is still running."""

        statement = (
            update(AutomationTriggerWebhook)
            .where(
                AutomationTriggerWebhook.automation_id == automation_id,
                AutomationTriggerWebhook.in_progress_event_hash == event_hash,
                AutomationTriggerWebhook.claim_token == claim_token,
            )
            .values(in_progress_until=lease_until)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return result_succeeded(result)
