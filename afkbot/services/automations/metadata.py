"""DTO mapping helpers for automation service responses."""

from __future__ import annotations

from typing import Literal

from afkbot.models.automation import Automation
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook
from afkbot.services.automations.contracts import (
    AutomationCronMetadata,
    AutomationMetadata,
    AutomationWebhookMetadata,
)
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.webhook_tokens import build_webhook_path, mask_webhook_token


def to_metadata(
    *,
    automation: Automation,
    cron: AutomationTriggerCron | None,
    webhook: AutomationTriggerWebhook | None,
    issued_webhook_token: str | None = None,
) -> AutomationMetadata:
    """Map repository automation row parts into public metadata DTO."""

    trigger_type = as_trigger_type(automation.trigger_type)
    status = as_status(automation.status)
    return AutomationMetadata(
        id=automation.id,
        profile_id=automation.profile_id,
        name=automation.name,
        prompt=automation.prompt,
        trigger_type=trigger_type,
        status=status,
        created_at=automation.created_at,
        updated_at=automation.updated_at,
        cron=None
        if cron is None
        else AutomationCronMetadata(
            cron_expr=cron.cron_expr,
            timezone=cron.timezone,
            next_run_at=cron.next_run_at,
            last_run_at=cron.last_run_at,
        ),
        webhook=None
        if webhook is None
        else AutomationWebhookMetadata(
            webhook_token=issued_webhook_token,
            webhook_path=build_webhook_path(issued_webhook_token),
            webhook_token_masked=mask_webhook_token(issued_webhook_token),
            last_received_at=webhook.last_received_at,
        ),
    )


def as_trigger_type(value: str) -> Literal["cron", "webhook"]:
    """Normalize persisted trigger type for API contracts."""

    if value == "cron":
        return "cron"
    if value == "webhook":
        return "webhook"
    raise AutomationsServiceError(
        error_code="invalid_trigger_type",
        reason=f"Unsupported trigger type: {value}",
    )


def as_status(value: str) -> Literal["active", "paused", "deleted"]:
    """Normalize persisted automation status for API contracts."""

    if value == "active":
        return "active"
    if value == "paused":
        return "paused"
    if value == "deleted":
        return "deleted"
    raise AutomationsServiceError(
        error_code="invalid_status",
        reason=f"Unsupported automation status: {value}",
    )
