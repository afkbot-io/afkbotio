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
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError, resolved_from_target
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.automations.contracts import (
    AutomationCronTickResult,
    AutomationMetadata,
    AutomationWebhookTriggerResult,
)
from afkbot.services.automations.cron_execution import tick_cron_automations
from afkbot.services.automations.delivery_target_codec import encode_delivery_target
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.loop_factory import AgentLoopLike
from afkbot.services.automations.metadata import to_metadata
from afkbot.services.automations.repository_support import ensure_profile_exists
from afkbot.services.automations.update_runtime import apply_automation_update
from afkbot.services.automations.validators import (
    compute_next_run_at,
    normalize_cron_expr,
    normalize_automation_prompt,
    normalize_delivery_mode,
    normalize_timezone_name,
    normalize_update_status,
    validate_create_payload,
)
from afkbot.services.automations.webhook_tokens import (
    hash_webhook_token,
    is_webhook_token_conflict,
)
from afkbot.services.automations.webhook_execution import trigger_webhook_automation
from afkbot.services.subagents.loader import SubagentLoader
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
        channel_delivery_service: ChannelDeliveryService | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine
        self._subagent_loader = SubagentLoader(settings) if settings is not None else None
        self._channel_delivery_service = (
            channel_delivery_service
            if channel_delivery_service is not None
            else (ChannelDeliveryService(settings) if settings is not None else None)
        )

    async def create_cron(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
        cron_expr: str,
        timezone_name: str = "UTC",
        delivery_mode: str | None = None,
        delivery_target: ChannelDeliveryTarget | None = None,
    ) -> AutomationMetadata:
        """Create profile automation with cron trigger metadata."""

        validate_create_payload(name=name, prompt=prompt)
        normalized_cron = normalize_cron_expr(cron_expr)
        normalized_timezone = normalize_timezone_name(timezone_name)
        _validate_delivery_target(delivery_target)
        normalized_delivery_mode = normalize_delivery_mode(
            delivery_mode,
            has_delivery_target=delivery_target is not None,
        )
        normalized_prompt = normalize_automation_prompt(
            prompt,
            delivery_mode=normalized_delivery_mode,
        )
        normalized_delivery_target = encode_delivery_target(delivery_target)
        now_utc = datetime.now(timezone.utc)
        next_run_at = compute_next_run_at(normalized_cron, now_utc)

        async def _op(repo: AutomationRepository) -> AutomationMetadata:
            await ensure_profile_exists(repo, profile_id)
            automation, cron = await repo.create_cron_automation(
                profile_id=profile_id,
                name=name.strip(),
                prompt=normalized_prompt,
                delivery_mode=normalized_delivery_mode,
                delivery_target_json=normalized_delivery_target,
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
        delivery_mode: str | None = None,
        delivery_target: ChannelDeliveryTarget | None = None,
    ) -> AutomationMetadata:
        """Create profile automation with generated webhook trigger token."""

        validate_create_payload(name=name, prompt=prompt)
        stripped_name = name.strip()
        _validate_delivery_target(delivery_target)
        normalized_delivery_mode = normalize_delivery_mode(
            delivery_mode,
            has_delivery_target=delivery_target is not None,
        )
        normalized_prompt = normalize_automation_prompt(
            prompt,
            delivery_mode=normalized_delivery_mode,
        )
        normalized_delivery_target = encode_delivery_target(delivery_target)
        for attempt in range(_WEBHOOK_TOKEN_ISSUE_ATTEMPTS):
            token = secrets.token_urlsafe(24)
            token_hash = hash_webhook_token(token)

            async def _op(repo: AutomationRepository) -> AutomationMetadata:
                await ensure_profile_exists(repo, profile_id)
                automation, webhook = await repo.create_webhook_automation(
                    profile_id=profile_id,
                    name=stripped_name,
                    prompt=normalized_prompt,
                    delivery_mode=normalized_delivery_mode,
                    delivery_target_json=normalized_delivery_target,
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
        delivery_mode: str | None = None,
        delivery_target: ChannelDeliveryTarget | None = None,
        clear_delivery_target: bool = False,
    ) -> AutomationMetadata:
        """Update one profile automation and trigger-specific fields."""

        if delivery_target is not None and clear_delivery_target:
            raise AutomationsServiceError(
                error_code="invalid_update_payload",
                reason="delivery_target and clear_delivery_target cannot be used together",
            )
        has_base_updates = any(field is not None for field in (name, prompt, status))
        has_cron_updates = cron_expr is not None or timezone_name is not None
        should_rotate_webhook_token = bool(rotate_webhook_token)
        has_delivery_target_update = delivery_target is not None or clear_delivery_target
        has_delivery_mode_update = delivery_mode is not None
        if (
            not has_base_updates
            and not has_cron_updates
            and not should_rotate_webhook_token
            and not has_delivery_target_update
            and not has_delivery_mode_update
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
        normalized_delivery_target = (
            None if clear_delivery_target else encode_delivery_target(delivery_target)
        )
        if not clear_delivery_target:
            _validate_delivery_target(delivery_target)

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
                requested_delivery_mode=delivery_mode,
                has_delivery_mode_update=has_delivery_mode_update,
                has_delivery_target_update=has_delivery_target_update,
                normalized_delivery_target_json=normalized_delivery_target,
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
        delivery_target: ChannelDeliveryTarget | None = None,
    ) -> AutomationWebhookTriggerResult:
        """Trigger one webhook automation and run its prompt through AgentLoop."""

        return await trigger_webhook_automation(
            session_factory=self._session_factory,
            with_repo=self._with_repo,
            subagent_loader=self._subagent_loader,
            token=token,
            payload=payload,
            agent_loop_factory=agent_loop_factory,
            delivery_target=delivery_target,
            delivery_service=self._channel_delivery_service,
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
            subagent_loader=self._subagent_loader,
            now_utc=now_utc,
            agent_loop_factory=agent_loop_factory,
            compute_next_run_at=compute_next_run_at,
            max_due_per_tick=max_due_per_tick,
            delivery_service=self._channel_delivery_service,
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


def _validate_delivery_target(target: ChannelDeliveryTarget | None) -> None:
    """Reject persisted automation targets that cannot be delivered by transport runtime."""

    if target is None:
        return
    try:
        resolved_from_target(target)
    except ChannelDeliveryServiceError as exc:
        raise AutomationsServiceError(
            error_code=exc.error_code,
            reason=exc.reason,
        ) from exc


def reset_automations_services() -> None:
    """Reset cached automations service instances (used by tests)."""

    _SERVICES_BY_ROOT.clear()


async def reset_automations_services_async() -> None:
    """Reset cached automations services and dispose owned async engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
