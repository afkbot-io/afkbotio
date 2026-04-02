"""Webhook-triggered automation execution runtime."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.models.automation import Automation
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.contracts import AutomationWebhookTriggerResult
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.lease_runtime import run_with_lease_refresh
from afkbot.services.automations.loop_factory import AgentLoopLike, build_automation_agent_loop
from afkbot.services.automations.message_factory import (
    compose_webhook_message,
    webhook_session_id,
)
from afkbot.services.automations.payloads import resolve_webhook_event_hash, sanitize_payload
from afkbot.services.automations.runtime_contracts import WithAutomationRepo
from afkbot.services.automations.runtime_target import build_automation_runtime_target

WEBHOOK_CLAIM_TTL = timedelta(minutes=15)


async def trigger_webhook_automation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    with_repo: WithAutomationRepo,
    profile_id: str,
    token: str,
    payload: Mapping[str, object],
    agent_loop_factory: Callable[..., AgentLoopLike],
) -> AutomationWebhookTriggerResult:
    """Trigger one webhook automation and run its prompt through AgentLoop."""

    now_utc = datetime.now(timezone.utc)
    normalized_profile_id = profile_id.strip()
    normalized_token = token.strip()
    event_hash = resolve_webhook_event_hash(payload)
    sanitized_payload = sanitize_payload(payload)
    lease_until = now_utc + WEBHOOK_CLAIM_TTL
    claim_token = secrets.token_hex(16)

    async def _claim_op(repo: AutomationRepository) -> tuple[Automation, bool]:
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
        accepted = await repo.claim_webhook_event(
            automation_id=webhook.automation_id,
            received_at=now_utc,
            event_hash=event_hash,
            lease_until=lease_until,
            claim_token=claim_token,
        )
        return (automation, accepted)

    automation, accepted = await with_repo(_claim_op)
    session_id = webhook_session_id(
        automation_id=automation.id,
        event_hash=event_hash,
    )
    if not accepted:
        return AutomationWebhookTriggerResult(
            automation_id=automation.id,
            profile_id=automation.profile_id,
            session_id=session_id,
            payload=sanitized_payload,
            deduplicated=True,
        )

    async with session_scope(session_factory) as session:
        loop = build_automation_agent_loop(
            agent_loop_factory=agent_loop_factory,
            session=session,
            profile_id=automation.profile_id,
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

        async def _release(repo: AutomationRepository) -> bool:
            return await repo.release_webhook_event(
                automation_id=automation.id,
                event_hash=event_hash,
                claim_token=claim_token,
            )

        async def _complete(repo: AutomationRepository) -> bool:
            return await repo.complete_webhook_event(
                automation_id=automation.id,
                event_hash=event_hash,
                claim_token=claim_token,
            )

        completed = False
        try:
            await run_with_lease_refresh(
                run=lambda: loop.run_turn(
                    profile_id=runtime_target.profile_id,
                    session_id=runtime_target.session_id,
                    message=message,
                    context_overrides=runtime_target.context_overrides,
                ),
                refresh=_refresh_webhook_lease,
                ttl=WEBHOOK_CLAIM_TTL,
            )
            completed = await with_repo(_complete)
        except asyncio.CancelledError as exc:
            released = await with_repo(_release)
            if not released:
                raise AutomationsServiceError(
                    error_code="automation_webhook_state_conflict",
                    reason="Failed to release webhook claim",
                ) from exc
            raise
        except Exception as exc:
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
