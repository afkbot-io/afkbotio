"""CRUD and query mixin for automation repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.automation import Automation
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook
from afkbot.repositories.automation_repo_common import AutomationRow, base_automation_join
from afkbot.repositories.support import profile_exists


class AutomationRepositoryCrudMixin:
    """Read/write automation and trigger rows without lease logic."""

    _session: AsyncSession

    async def create_cron_automation(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
        cron_expr: str,
        timezone: str,
        next_run_at: datetime | None,
    ) -> tuple[Automation, AutomationTriggerCron]:
        """Create automation row with cron trigger row."""

        automation = Automation(
            profile_id=profile_id,
            name=name,
            prompt=prompt,
            trigger_type="cron",
            status="active",
        )
        self._session.add(automation)
        await self._session.flush()

        cron = AutomationTriggerCron(
            automation_id=automation.id,
            cron_expr=cron_expr,
            timezone=timezone,
            next_run_at=next_run_at,
        )
        self._session.add(cron)
        await self._session.flush()
        await self._session.refresh(automation)
        await self._session.refresh(cron)
        return automation, cron

    async def create_webhook_automation(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
        webhook_token_hash: str,
    ) -> tuple[Automation, AutomationTriggerWebhook]:
        """Create automation row with webhook trigger row."""

        automation = Automation(
            profile_id=profile_id,
            name=name,
            prompt=prompt,
            trigger_type="webhook",
            status="active",
        )
        self._session.add(automation)
        await self._session.flush()

        webhook = AutomationTriggerWebhook(
            automation_id=automation.id,
            webhook_token_hash=webhook_token_hash,
        )
        self._session.add(webhook)
        await self._session.flush()
        await self._session.refresh(automation)
        await self._session.refresh(webhook)
        return automation, webhook

    async def get_by_id(self, *, profile_id: str, automation_id: int) -> AutomationRow | None:
        """Return one automation with optional trigger rows for a profile."""

        statement: Select[
            tuple[Automation, AutomationTriggerCron | None, AutomationTriggerWebhook | None]
        ] = base_automation_join().where(
            Automation.profile_id == profile_id,
            Automation.id == automation_id,
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return (row[0], row[1], row[2])

    async def list_by_profile(
        self,
        *,
        profile_id: str,
        include_deleted: bool = False,
    ) -> list[AutomationRow]:
        """List automations for profile with optional deleted rows."""

        statement = base_automation_join().where(Automation.profile_id == profile_id)
        if not include_deleted:
            statement = statement.where(Automation.status != "deleted")
        statement = statement.order_by(Automation.id.asc())
        result = await self._session.execute(statement)
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def soft_delete(self, *, profile_id: str, automation_id: int) -> bool:
        """Mark one profile automation as deleted."""

        row = await self.get_by_id(profile_id=profile_id, automation_id=automation_id)
        if row is None:
            return False
        automation, _, _ = row
        if automation.status == "deleted":
            return False
        automation.status = "deleted"
        await self._session.flush()
        return True

    async def update_automation(
        self,
        *,
        profile_id: str,
        automation_id: int,
        name: str | None = None,
        prompt: str | None = None,
        status: str | None = None,
    ) -> Automation | None:
        """Update base automation fields for one profile automation."""

        statement: Select[tuple[Automation]] = select(Automation).where(
            Automation.profile_id == profile_id,
            Automation.id == automation_id,
        )
        automation = (await self._session.execute(statement)).scalar_one_or_none()
        if automation is None:
            return None
        if name is not None:
            automation.name = name
        if prompt is not None:
            automation.prompt = prompt
        if status is not None:
            automation.status = status
        await self._session.flush()
        await self._session.refresh(automation)
        return automation

    async def touch_automation(
        self,
        *,
        profile_id: str,
        automation_id: int,
    ) -> Automation | None:
        """Update only automation.updated_at for one profile automation."""

        statement: Select[tuple[Automation]] = select(Automation).where(
            Automation.profile_id == profile_id,
            Automation.id == automation_id,
        )
        automation = (await self._session.execute(statement)).scalar_one_or_none()
        if automation is None:
            return None
        automation.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(automation)
        return automation

    async def update_cron_trigger(
        self,
        *,
        automation_id: int,
        cron_expr: str | None = None,
        timezone_name: str | None = None,
        next_run_at: datetime | None = None,
    ) -> AutomationTriggerCron | None:
        """Update cron trigger fields for one automation id."""

        cron = cast(
            AutomationTriggerCron | None,
            await self._session.get(AutomationTriggerCron, automation_id),
        )
        if cron is None:
            return None
        if cron_expr is not None:
            cron.cron_expr = cron_expr
        if timezone_name is not None:
            cron.timezone = timezone_name
        if next_run_at is not None:
            cron.next_run_at = next_run_at
        await self._session.flush()
        await self._session.refresh(cron)
        return cron

    async def update_webhook_trigger(
        self,
        *,
        automation_id: int,
        webhook_token_hash: str | None = None,
    ) -> AutomationTriggerWebhook | None:
        """Update webhook trigger mutable fields for one automation id."""

        webhook = cast(
            AutomationTriggerWebhook | None,
            await self._session.get(AutomationTriggerWebhook, automation_id),
        )
        if webhook is None:
            return None
        if webhook_token_hash is not None:
            webhook.webhook_token_hash = webhook_token_hash
        await self._session.flush()
        await self._session.refresh(webhook)
        return webhook

    async def find_webhook_by_token(
        self,
        *,
        token_hash: str,
    ) -> tuple[Automation, AutomationTriggerWebhook] | None:
        """Find active non-deleted webhook automation by token."""

        statement: Select[tuple[Automation, AutomationTriggerWebhook]] = (
            select(Automation, AutomationTriggerWebhook)
            .join(AutomationTriggerWebhook, AutomationTriggerWebhook.automation_id == Automation.id)
            .where(
                AutomationTriggerWebhook.webhook_token_hash == token_hash,
                Automation.status == "active",
                Automation.trigger_type == "webhook",
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return (row[0], row[1])

    async def list_due_cron(
        self,
        *,
        now_utc: datetime,
        limit: int | None = None,
    ) -> list[tuple[Automation, AutomationTriggerCron]]:
        """List active cron automations due for execution at `now_utc`."""

        statement: Select[tuple[Automation, AutomationTriggerCron]] = (
            select(Automation, AutomationTriggerCron)
            .join(AutomationTriggerCron, AutomationTriggerCron.automation_id == Automation.id)
            .where(
                Automation.status == "active",
                Automation.trigger_type == "cron",
                or_(
                    AutomationTriggerCron.next_run_at.is_(None),
                    AutomationTriggerCron.next_run_at <= now_utc,
                ),
                or_(
                    AutomationTriggerCron.claimed_until.is_(None),
                    AutomationTriggerCron.claimed_until <= now_utc,
                ),
            )
            .order_by(
                AutomationTriggerCron.last_run_at.asc().nullsfirst(),
                AutomationTriggerCron.next_run_at.asc().nullsfirst(),
                Automation.id.asc(),
            )
        )
        if limit is not None:
            statement = statement.limit(max(1, int(limit)))
        result = await self._session.execute(statement)
        return [(row[0], row[1]) for row in result.all()]

    async def validate_profile_exists(self, profile_id: str) -> bool:
        """Return True when profile exists in storage."""

        return await profile_exists(self._session, profile_id=profile_id)
