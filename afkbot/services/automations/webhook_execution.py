"""Webhook-triggered automation execution runtime."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.models.automation import Automation
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.contracts import AutomationWebhookTriggerResult
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.lease_runtime import run_with_lease_refresh
from afkbot.services.automations.message_factory import (
    compose_webhook_message,
    webhook_session_id,
)
from afkbot.services.automations.payloads import resolve_webhook_event_hash, sanitize_payload
from afkbot.services.automations.runtime_contracts import WithAutomationRepo
from afkbot.services.automations.runtime_target import build_automation_runtime_target
from afkbot.services.automations.session_runner_factory import (
    AutomationSessionRunnerFactory,
    build_automation_session_runner,
)
from afkbot.settings import Settings

WEBHOOK_CLAIM_TTL = timedelta(minutes=15)


async def trigger_webhook_automation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    with_repo: WithAutomationRepo,
    profile_id: str,
    token: str,
    payload: Mapping[str, object],
    settings: Settings,
    session_runner_factory: AutomationSessionRunnerFactory | None,
    run_timeout_sec: float | None = None,
) -> AutomationWebhookTriggerResult:
    """Trigger one webhook automation and run its prompt through AgentLoop."""

    now_utc = datetime.now(timezone.utc)
    normalized_profile_id = profile_id.strip()
    normalized_token = token.strip()
    event_hash = resolve_webhook_event_hash(payload)
    sanitized_payload = sanitize_payload(payload)
    lease_until = now_utc + WEBHOOK_CLAIM_TTL
    claim_token = secrets.token_hex(16)

    async def _claim_op(repo: AutomationRepository) -> tuple[Automation, bool, str]:
        row = await repo.find_webhook_by_target(
            profile_id=normalized_profile_id,
            token=normalized_token,
        )
        if row is None:
            raise AutomationsServiceError(
                error_code="automation_not_found",
                reason="Automation webhook token not found",
            )
        automation, webhook = row
        session_id = webhook_session_id(
            automation_id=automation.id,
            event_hash=event_hash,
        )
        accepted = await repo.claim_webhook_event(
            automation_id=webhook.automation_id,
            received_at=now_utc,
            event_hash=event_hash,
            lease_until=lease_until,
            claim_token=claim_token,
            session_id=session_id,
        )
        return (automation, accepted, session_id)

    automation, accepted, session_id = await with_repo(_claim_op)
    if not accepted:
        return AutomationWebhookTriggerResult(
            automation_id=automation.id,
            profile_id=automation.profile_id,
            session_id=session_id,
            payload=sanitized_payload,
            deduplicated=True,
        )

    runner = build_automation_session_runner(
        session_factory=session_factory,
        profile_id=automation.profile_id,
        settings=settings,
        runner_factory=session_runner_factory,
    )
    message = compose_webhook_message(
        automation.prompt,
        sanitized_payload,
    )
    runtime_target = build_automation_runtime_target(
        profile_id=automation.profile_id,
        session_id=session_id,
        automation_id=automation.id,
        trigger_type="webhook",
        event_hash=event_hash,
        payload=sanitized_payload,
    )

    async def _refresh_webhook_lease() -> bool:
        async def _refresh(repo: AutomationRepository) -> bool:
            return await repo.refresh_webhook_claim(
                automation_id=automation.id,
                event_hash=event_hash,
                claim_token=claim_token,
                lease_until=datetime.now(timezone.utc) + WEBHOOK_CLAIM_TTL,
            )

        return await with_repo(_refresh)

    async def _mark_started(repo: AutomationRepository) -> bool:
        return await repo.mark_webhook_started(
            automation_id=automation.id,
            event_hash=event_hash,
            claim_token=claim_token,
            started_at=datetime.now(timezone.utc),
        )

    async def _release(repo: AutomationRepository) -> bool:
        return await repo.release_webhook_event(
            automation_id=automation.id,
            event_hash=event_hash,
            claim_token=claim_token,
            failed_at=datetime.now(timezone.utc),
            error_message=_format_webhook_execution_error(exc_info),
        )

    async def _complete(repo: AutomationRepository) -> bool:
        return await repo.complete_webhook_event(
            automation_id=automation.id,
            event_hash=event_hash,
            claim_token=claim_token,
            completed_at=datetime.now(timezone.utc),
        )

    completed = False
    exc_info: BaseException | None = None
    try:
        started = await with_repo(_mark_started)
        if not started:
            raise AutomationsServiceError(
                error_code="automation_webhook_state_conflict",
                reason="Failed to mark webhook execution as started",
            )
        await run_with_lease_refresh(
            run=lambda: runner.run_turn(
                profile_id=runtime_target.profile_id,
                session_id=runtime_target.session_id,
                message=message,
                context_overrides=runtime_target.context_overrides,
                source="automation",
            ),
            refresh=_refresh_webhook_lease,
            ttl=WEBHOOK_CLAIM_TTL,
            timeout_sec=run_timeout_sec,
        )
        completed = await with_repo(_complete)
    except asyncio.CancelledError as exc:
        exc_info = exc
        released = await with_repo(_release)
        if not released:
            raise AutomationsServiceError(
                error_code="automation_webhook_state_conflict",
                reason="Failed to release webhook claim",
            ) from exc
        raise
    except Exception as exc:
        exc_info = exc
        released = await with_repo(_release)
        if not released:
            raise AutomationsServiceError(
                error_code="automation_webhook_state_conflict",
                reason="Failed to release webhook claim",
            ) from exc
        raise

    if not completed:
        raise AutomationsServiceError(
            error_code="automation_webhook_state_conflict",
            reason="Failed to finalize webhook claim",
        )
    return AutomationWebhookTriggerResult(
        automation_id=automation.id,
        profile_id=automation.profile_id,
        session_id=session_id,
        payload=sanitized_payload,
    )


def _format_webhook_execution_error(exc: BaseException | None) -> str:
    """Build a compact persisted error string for failed webhook executions."""

    if exc is None:
        return "Webhook execution failed"
    if isinstance(exc, AutomationsServiceError):
        return f"{exc.error_code}: {exc.reason}"[:2000]
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"[:2000]
    return type(exc).__name__[:2000]
