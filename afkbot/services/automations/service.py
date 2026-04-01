"""Automation service orchestration over repository and AgentLoop execution."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.contracts import (
    AutomationCronTickResult,
    AutomationMetadata,
    AutomationWebhookTriggerResult,
)
from afkbot.services.automations.cron_execution import tick_cron_automations
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.loop_factory import AgentLoopLike
from afkbot.services.automations.metadata import to_metadata
from afkbot.services.automations.repository_support import ensure_profile_exists
from afkbot.services.automations.update_runtime import apply_automation_update
from afkbot.services.automations.validators import (
    compute_next_run_at,
    normalize_cron_expr,
    normalize_automation_prompt,
    normalize_timezone_name,
    normalize_update_status,
    validate_create_payload,
)
from afkbot.services.automations.webhook_tokens import (
    hash_webhook_token,
    is_webhook_token_conflict,
)
from afkbot.services.automations.webhook_execution import trigger_webhook_automation
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "AutomationsService"] = {}
TValue = TypeVar("TValue")
_WEBHOOK_TOKEN_ISSUE_ATTEMPTS = 5

__all__ = [
    "AutomationsService",
    "AutomationsServiceError",
    "get_automations_service",
    "reset_automations_services",
]


class AutomationsService:
    """Service for automation CRUD and trigger execution flows."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        """Capture storage/session dependencies for automation operations."""

        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine

    async def create_cron(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
        cron_expr: str,
        timezone_name: str = "UTC",
    ) -> AutomationMetadata:
        """Create profile automation with cron trigger metadata."""

        validate_create_payload(name=name, prompt=prompt)
        normalized_cron = normalize_cron_expr(cron_expr)
        normalized_timezone = normalize_timezone_name(timezone_name)
        normalized_prompt = normalize_automation_prompt(prompt)
        now_utc = datetime.now(timezone.utc)
        next_run_at = compute_next_run_at(normalized_cron, now_utc)

        async def _op(repo: AutomationRepository) -> AutomationMetadata:
            await ensure_profile_exists(repo, profile_id)
            automation, cron = await repo.create_cron_automation(
                profile_id=profile_id,
                name=name.strip(),
                prompt=normalized_prompt,
                cron_expr=normalized_cron,
                timezone=normalized_timezone,
                next_run_at=next_run_at,
            )
            return to_metadata(automation=automation, cron=cron, webhook=None)

        return await self._with_repo(_op)

    async def create_webhook(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
    ) -> AutomationMetadata:
        """Create profile automation with generated webhook trigger token."""

        validate_create_payload(name=name, prompt=prompt)
        stripped_name = name.strip()
        normalized_prompt = normalize_automation_prompt(prompt)
        for attempt in range(_WEBHOOK_TOKEN_ISSUE_ATTEMPTS):
            token = secrets.token_urlsafe(24)
            token_hash = hash_webhook_token(token)

            async def _op(repo: AutomationRepository) -> AutomationMetadata:
                await ensure_profile_exists(repo, profile_id)
                automation, webhook = await repo.create_webhook_automation(
                    profile_id=profile_id,
                    name=stripped_name,
                    prompt=normalized_prompt,
                    webhook_token_hash=token_hash,
                )
                return to_metadata(
                    automation=automation,
                    cron=None,
                    webhook=webhook,
                    issued_webhook_token=token,
                )

            try:
                return await self._with_repo(_op)
            except IntegrityError as exc:
                if not is_webhook_token_conflict(exc):
                    raise
                if attempt + 1 == _WEBHOOK_TOKEN_ISSUE_ATTEMPTS:
                    raise AutomationsServiceError(
                        error_code="automation_webhook_token_conflict",
                        reason="Webhook token generation conflict",
                    ) from None
        raise AutomationsServiceError(
            error_code="automation_webhook_token_conflict",
            reason="Webhook token generation conflict",
        )

    async def get(self, *, profile_id: str, automation_id: int) -> AutomationMetadata:
        """Get one automation metadata by profile/id."""

        async def _op(repo: AutomationRepository) -> AutomationMetadata:
            await ensure_profile_exists(repo, profile_id)
            row = await repo.get_by_id(profile_id=profile_id, automation_id=automation_id)
            if row is None or row[0].status == "deleted":
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            return to_metadata(automation=row[0], cron=row[1], webhook=row[2])

        return await self._with_repo(_op)

    async def list(
        self,
        *,
        profile_id: str,
        include_deleted: bool = False,
    ) -> list[AutomationMetadata]:
        """List profile automations with optional deleted rows."""

        async def _op(repo: AutomationRepository) -> list[AutomationMetadata]:
            await ensure_profile_exists(repo, profile_id)
            rows = await repo.list_by_profile(
                profile_id=profile_id,
                include_deleted=include_deleted,
            )
            return [
                to_metadata(automation=automation, cron=cron, webhook=webhook)
                for automation, cron, webhook in rows
            ]

        return await self._with_repo(_op)

    async def delete(self, *, profile_id: str, automation_id: int) -> bool:
        """Soft-delete one profile automation by id."""

        async def _op(repo: AutomationRepository) -> bool:
            await ensure_profile_exists(repo, profile_id)
            deleted = await repo.soft_delete(profile_id=profile_id, automation_id=automation_id)
            if not deleted:
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            return True

        return await self._with_repo(_op)

    async def update(
        self,
        *,
        profile_id: str,
        automation_id: int,
        name: str | None = None,
        prompt: str | None = None,
        status: str | None = None,
        cron_expr: str | None = None,
        timezone_name: str | None = None,
        rotate_webhook_token: bool | None = None,
    ) -> AutomationMetadata:
        """Update one profile automation and trigger-specific fields."""
        has_base_updates = any(field is not None for field in (name, prompt, status))
        has_cron_updates = cron_expr is not None or timezone_name is not None
        should_rotate_webhook_token = bool(rotate_webhook_token)
        if (
            not has_base_updates
            and not has_cron_updates
            and not should_rotate_webhook_token
        ):
            raise AutomationsServiceError(
                error_code="invalid_update_payload",
                reason="At least one update field is required",
            )

        normalized_name: str | None = None
        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise AutomationsServiceError(
                    error_code="invalid_name",
                    reason="Name is required",
                )

        normalized_prompt: str | None = None
        if prompt is not None:
            normalized_prompt = prompt.strip()
            if not normalized_prompt:
                raise AutomationsServiceError(
                    error_code="invalid_prompt",
                    reason="Prompt is required",
                )

        normalized_status = normalize_update_status(status) if status is not None else None
        normalized_cron = normalize_cron_expr(cron_expr) if cron_expr is not None else None
        normalized_timezone = (
            normalize_timezone_name(timezone_name) if timezone_name is not None else None
        )

        async def _run_update(issued_webhook_token: str | None) -> AutomationMetadata:
            return await apply_automation_update(
                with_repo=self._with_repo,
                profile_id=profile_id,
                automation_id=automation_id,
                has_base_updates=has_base_updates,
                has_cron_updates=has_cron_updates,
                should_rotate_webhook_token=should_rotate_webhook_token,
                normalized_name=normalized_name,
                normalized_prompt=normalized_prompt,
                normalized_status=normalized_status,
                normalized_cron=normalized_cron,
                normalized_timezone=normalized_timezone,
                issued_webhook_token=issued_webhook_token,
                to_metadata=to_metadata,
                compute_next_run_at=compute_next_run_at,
                hash_webhook_token=hash_webhook_token,
            )

        if not should_rotate_webhook_token:
            return await _run_update(None)

        for attempt in range(_WEBHOOK_TOKEN_ISSUE_ATTEMPTS):
            issued_webhook_token = secrets.token_urlsafe(24)
            try:
                return await _run_update(issued_webhook_token)
            except IntegrityError as exc:
                if not is_webhook_token_conflict(exc):
                    raise
                if attempt + 1 == _WEBHOOK_TOKEN_ISSUE_ATTEMPTS:
                    raise AutomationsServiceError(
                        error_code="automation_webhook_token_conflict",
                        reason="Webhook token rotation conflict",
                    ) from None
        raise AutomationsServiceError(
            error_code="automation_webhook_token_conflict",
            reason="Webhook token rotation conflict",
        )

    async def trigger_webhook(
        self,
        *,
        token: str,
        payload: Mapping[str, object],
        agent_loop_factory: Callable[..., AgentLoopLike],
    ) -> AutomationWebhookTriggerResult:
        """Trigger one webhook automation and run its prompt through AgentLoop."""

        return await trigger_webhook_automation(
            session_factory=self._session_factory,
            with_repo=self._with_repo,
            token=token,
            payload=payload,
            agent_loop_factory=agent_loop_factory,
        )

    async def tick_cron(
        self,
        *,
        now_utc: datetime,
        agent_loop_factory: Callable[..., AgentLoopLike],
        max_due_per_tick: int | None = None,
    ) -> AutomationCronTickResult:
        """Run due cron automations and update their next execution timestamp."""

        return await tick_cron_automations(
            session_factory=self._session_factory,
            with_repo=self._with_repo,
            now_utc=now_utc,
            agent_loop_factory=agent_loop_factory,
            compute_next_run_at=compute_next_run_at,
            max_due_per_tick=max_due_per_tick,
        )

    async def _with_repo(
        self,
        op: Callable[[AutomationRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = AutomationRepository(session)
            return await op(repo)

    async def shutdown(self) -> None:
        """Dispose owned async engine when the service created it."""

        if self._engine is None:
            return
        await self._engine.dispose()


def get_automations_service(settings: Settings) -> AutomationsService:
    """Get or create one automations service instance for current root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        service = AutomationsService(
            session_factory=session_factory,
            settings=settings,
            engine=engine,
        )
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_automations_services() -> None:
    """Reset cached automations service instances (used by tests)."""

    _SERVICES_BY_ROOT.clear()


async def reset_automations_services_async() -> None:
    """Reset cached automations services and dispose owned async engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
