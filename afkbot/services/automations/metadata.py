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
from afkbot.services.automations.webhook_tokens import (
    build_webhook_path,
    build_webhook_url,
    mask_webhook_token,
)


def to_metadata(
    *,
    automation: Automation,
    cron: AutomationTriggerCron | None,
    webhook: AutomationTriggerWebhook | None,
    runtime_base_url: str | None = None,
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
            webhook_token=webhook.webhook_token,
            webhook_path=build_webhook_path(automation.profile_id, webhook.webhook_token),
            webhook_url=build_webhook_url(
                runtime_base_url,
                automation.profile_id,
                webhook.webhook_token,
            ),
            webhook_token_masked=mask_webhook_token(webhook.webhook_token),
            last_execution_status=_resolve_webhook_execution_status(webhook),
            last_received_at=webhook.last_received_at,
            last_started_at=webhook.last_started_at,
            last_succeeded_at=webhook.last_succeeded_at,
            last_failed_at=webhook.last_failed_at,
            last_error=webhook.last_error,
            last_session_id=webhook.last_session_id,
            last_event_hash=webhook.last_event_hash,
            chat_resume_command=_build_chat_resume_command(
                profile_id=automation.profile_id,
                session_id=webhook.last_session_id,
            ),
        ),
    )


def _resolve_webhook_execution_status(
    webhook: AutomationTriggerWebhook,
) -> Literal["idle", "received", "running", "succeeded", "failed"]:
    """Derive user-facing execution status from persisted webhook trigger state."""

    if webhook.in_progress_event_hash:
        return "running"
    if webhook.last_succeeded_at is not None and (
        webhook.last_failed_at is None or webhook.last_succeeded_at >= webhook.last_failed_at
    ):
        return "succeeded"
    if webhook.last_failed_at is not None:
        return "failed"
    if webhook.last_received_at is not None:
        return "received"
    return "idle"


def _build_chat_resume_command(*, profile_id: str, session_id: str | None) -> str | None:
    """Build a ready-to-copy CLI command for resuming the automation chat session."""

    normalized_session = (session_id or "").strip()
    if not normalized_session:
        return None
    return f"afk chat --profile {profile_id} --session {normalized_session}"


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
