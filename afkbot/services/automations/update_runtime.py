"""Update flow helpers for automation metadata and trigger mutations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.contracts import AutomationMetadata
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.repository_support import ensure_profile_exists
from afkbot.services.automations.runtime_contracts import WithAutomationRepo
from afkbot.services.automations.validators import normalize_automation_prompt


async def apply_automation_update(
    *,
    with_repo: WithAutomationRepo,
    profile_id: str,
    automation_id: int,
    has_base_updates: bool,
    has_cron_updates: bool,
    should_rotate_webhook_token: bool,
    normalized_name: str | None,
    normalized_prompt: str | None,
    normalized_status: Literal["active", "paused"] | None,
    normalized_cron: str | None,
    normalized_timezone: str | None,
    issued_webhook_token: str | None,
    to_metadata: Callable[..., AutomationMetadata],
    compute_next_run_at: Callable[[str, datetime], datetime],
    hash_webhook_token: Callable[[str], str],
) -> AutomationMetadata:
    """Apply one validated automation update inside repository scope."""

    issued_token_hash = (
        None if issued_webhook_token is None else hash_webhook_token(issued_webhook_token)
    )

    async def _op(repo: AutomationRepository) -> AutomationMetadata:
        await ensure_profile_exists(repo, profile_id)
        row = await repo.get_by_id(profile_id=profile_id, automation_id=automation_id)
        if row is None:
            raise AutomationsServiceError(
                error_code="automation_not_found",
                reason="Automation not found",
            )
        automation, cron, webhook = row
        if automation.status == "deleted":
            raise AutomationsServiceError(
                error_code="invalid_status",
                reason="Deleted automation cannot be updated",
            )

        if automation.trigger_type == "cron":
            if should_rotate_webhook_token:
                raise AutomationsServiceError(
                    error_code="invalid_update_payload",
                    reason="rotate_webhook_token is supported only for webhook trigger",
                )
        elif automation.trigger_type == "webhook":
            if has_cron_updates:
                raise AutomationsServiceError(
                    error_code="invalid_update_payload",
                    reason="cron_expr/timezone_name are supported only for cron trigger",
                )
        else:
            raise AutomationsServiceError(
                error_code="invalid_trigger_type",
                reason=f"Unsupported trigger type: {automation.trigger_type}",
            )

        effective_prompt = normalize_automation_prompt(
            normalized_prompt if normalized_prompt is not None else automation.prompt
        )
        has_effective_prompt_update = (
            normalized_prompt is not None or effective_prompt != automation.prompt
        )

        if has_base_updates or has_effective_prompt_update:
            updated_automation = await repo.update_automation(
                profile_id=profile_id,
                automation_id=automation_id,
                name=normalized_name,
                prompt=effective_prompt if has_effective_prompt_update else None,
                status=normalized_status,
            )
            if updated_automation is None:
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            automation = updated_automation

        trigger_updated = False
        if automation.trigger_type == "cron" and has_cron_updates:
            if cron is None:
                raise AutomationsServiceError(
                    error_code="invalid_trigger_type",
                    reason="Cron trigger metadata is missing",
                )
            effective_cron_expr = normalized_cron or cron.cron_expr
            next_run_at = compute_next_run_at(
                effective_cron_expr,
                datetime.now(timezone.utc),
            )
            updated_cron = await repo.update_cron_trigger(
                automation_id=automation_id,
                cron_expr=normalized_cron,
                timezone_name=normalized_timezone,
                next_run_at=next_run_at,
            )
            if updated_cron is None:
                raise AutomationsServiceError(
                    error_code="invalid_trigger_type",
                    reason="Cron trigger metadata is missing",
                )
            cron = updated_cron
            trigger_updated = True

        if automation.trigger_type == "webhook" and issued_token_hash is not None:
            if webhook is None:
                raise AutomationsServiceError(
                    error_code="invalid_trigger_type",
                    reason="Webhook trigger metadata is missing",
                )
            updated_webhook = await repo.update_webhook_trigger(
                automation_id=automation_id,
                webhook_token=issued_webhook_token,
                webhook_token_hash=issued_token_hash,
            )
            if updated_webhook is None:
                raise AutomationsServiceError(
                    error_code="invalid_trigger_type",
                    reason="Webhook trigger metadata is missing",
                )
            webhook = updated_webhook
            trigger_updated = True

        if trigger_updated:
            touched = await repo.touch_automation(
                profile_id=profile_id,
                automation_id=automation_id,
            )
            if touched is None:
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            automation = touched

        return to_metadata(
            automation=automation,
            cron=cron,
            webhook=webhook,
        )

    return await with_repo(_op)
