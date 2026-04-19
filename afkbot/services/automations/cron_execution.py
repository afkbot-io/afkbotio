"""Cron-triggered automation execution runtime."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.contracts import AutomationCronTickResult
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.lease_runtime import run_with_lease_refresh
from afkbot.services.automations.message_factory import (
    compose_cron_message,
    cron_session_id,
)
from afkbot.services.automations.runtime_contracts import WithAutomationRepo
from afkbot.services.automations.runtime_target import build_automation_runtime_target
from afkbot.services.automations.session_runner_factory import (
    AutomationSessionRunnerFactory,
    build_automation_session_runner,
)
from afkbot.settings import Settings

CRON_CLAIM_TTL = timedelta(minutes=15)
_LOGGER = logging.getLogger(__name__)


async def tick_cron_automations(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    with_repo: WithAutomationRepo,
    now_utc: datetime,
    settings: Settings,
    session_runner_factory: AutomationSessionRunnerFactory | None,
    compute_next_run_at: Callable[[str, datetime, str], datetime],
    max_due_per_tick: int | None = None,
    run_timeout_sec: float | None = None,
) -> AutomationCronTickResult:
    """Run due cron automations and update their next execution timestamp."""

    if now_utc.tzinfo is None:
        raise AutomationsServiceError(
            error_code="invalid_datetime",
            reason="now_utc must be timezone-aware",
        )
    normalized_now = now_utc.astimezone(timezone.utc)

    normalized_limit = None
    if max_due_per_tick is not None:
        normalized_limit = max(1, int(max_due_per_tick))

    async def _due_rows(
        repo: AutomationRepository,
    ) -> list[tuple[int, str, str, str, str]]:
        rows = await repo.list_due_cron(
            now_utc=normalized_now,
            limit=normalized_limit,
        )
        return [
            (
                automation.id,
                automation.profile_id,
                automation.prompt,
                cron.cron_expr,
                cron.timezone,
            )
            for automation, cron in rows
        ]

    due_rows = await with_repo(_due_rows)
    triggered_ids: list[int] = []
    failed_ids: list[int] = []
    for automation_id, profile_id, prompt, cron_expr, timezone_name in due_rows:
        claim_token = secrets.token_hex(16)
        message = compose_cron_message(
            prompt,
        )
        runtime_target = build_automation_runtime_target(
            profile_id=profile_id,
            session_id=cron_session_id(
                automation_id=automation_id,
                claim_token=claim_token,
            ),
            automation_id=automation_id,
            trigger_type="cron",
            cron_expr=cron_expr,
        )

        async def _claim(repo: AutomationRepository) -> bool:
            return await repo.claim_cron_execution(
                automation_id=automation_id,
                due_before_or_at=normalized_now,
                claim_until=datetime.now(timezone.utc) + CRON_CLAIM_TTL,
                claim_token=claim_token,
            )

        claimed = await with_repo(_claim)
        if not claimed:
            continue

        async def _refresh_cron_lease() -> bool:
            async def _refresh(repo: AutomationRepository) -> bool:
                return await repo.refresh_cron_claim(
                    automation_id=automation_id,
                    claim_token=claim_token,
                    claim_until=datetime.now(timezone.utc) + CRON_CLAIM_TTL,
                )

            return await with_repo(_refresh)

        async def _run() -> object:
            runner = build_automation_session_runner(
                session_factory=session_factory,
                profile_id=profile_id,
                settings=settings,
                runner_factory=session_runner_factory,
            )
            return await runner.run_turn(
                profile_id=runtime_target.profile_id,
                session_id=runtime_target.session_id,
                message=message,
                context_overrides=runtime_target.context_overrides,
                source="automation",
            )

        try:
            next_run_at = compute_next_run_at(cron_expr, normalized_now, timezone_name)
            await run_with_lease_refresh(
                run=_run,
                refresh=_refresh_cron_lease,
                ttl=CRON_CLAIM_TTL,
                timeout_sec=run_timeout_sec,
            )

            async def _complete(repo: AutomationRepository) -> bool:
                return await repo.mark_cron_executed(
                    automation_id=automation_id,
                    next_run_at=next_run_at,
                    executed_at=normalized_now,
                    claim_token=claim_token,
                )

            completed = await with_repo(_complete)
            if not completed:
                raise AutomationsServiceError(
                    error_code="automation_cron_state_conflict",
                    reason="Failed to finalize cron claim",
                )
            triggered_ids.append(automation_id)
            _LOGGER.info(
                "automation_cron_triggered automation_id=%s profile_id=%s next_run_at=%s",
                automation_id,
                profile_id,
                next_run_at.isoformat(),
            )
        except asyncio.CancelledError:
            failed_ids.append(automation_id)

            async def _release(repo: AutomationRepository) -> bool:
                return await repo.release_cron_claim(
                    automation_id=automation_id,
                    claim_token=claim_token,
                )

            await with_repo(_release)
            raise
        except Exception:
            failed_ids.append(automation_id)
            _LOGGER.exception(
                "automation_cron_failed automation_id=%s profile_id=%s",
                automation_id,
                profile_id,
            )

            async def _release(repo: AutomationRepository) -> bool:
                return await repo.release_cron_claim(
                    automation_id=automation_id,
                    claim_token=claim_token,
                )

            await with_repo(_release)

    return AutomationCronTickResult(triggered_ids=triggered_ids, failed_ids=failed_ids)
