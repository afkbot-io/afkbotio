"""Shared types and SQL helpers for automation repository."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Select, and_, or_, select

from afkbot.models.automation import Automation
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook

AutomationRow = tuple[Automation, AutomationTriggerCron | None, AutomationTriggerWebhook | None]


def base_automation_join() -> Select[
    tuple[Automation, AutomationTriggerCron | None, AutomationTriggerWebhook | None]
]:
    """Build canonical automation + trigger outer join used by repository queries."""

    return cast(
        Select[
            tuple[Automation, AutomationTriggerCron | None, AutomationTriggerWebhook | None]
        ],
        select(Automation, AutomationTriggerCron, AutomationTriggerWebhook)
        .outerjoin(AutomationTriggerCron, AutomationTriggerCron.automation_id == Automation.id)
        .outerjoin(
            AutomationTriggerWebhook,
            AutomationTriggerWebhook.automation_id == Automation.id,
        )
        .where(
            and_(
                or_(
                    Automation.trigger_type == "cron",
                    AutomationTriggerCron.automation_id.is_(None),
                ),
                or_(
                    Automation.trigger_type == "webhook",
                    AutomationTriggerWebhook.automation_id.is_(None),
                ),
            )
        ),
    )


def result_succeeded(result: object) -> bool:
    """Return True when update/claim statement affected at least one row."""

    rowcount = int(getattr(result, "rowcount", 0) or 0)
    return rowcount > 0
