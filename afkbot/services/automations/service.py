"""Automation service orchestration over repository and session turn execution."""

from __future__ import annotations

import builtins
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.automation import Automation
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.contracts import (
    AutomationCronTickResult,
    AutomationMetadata,
    AutomationWebhookEndpointMetadata,
    AutomationWebhookTriggerResult,
)
from afkbot.services.automations.cron_execution import tick_cron_automations
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.graph.contracts import (
    AutomationGraphMetadata,
    AutomationGraphRunMetadata,
    AutomationGraphSpec,
    AutomationGraphTraceMetadata,
    AutomationGraphValidationReport,
)
from afkbot.services.automations.graph.executor import AutomationGraphSubagentFactory
from afkbot.services.automations.graph.service import AutomationGraphService
from afkbot.services.automations.metadata import to_metadata
from afkbot.services.automations.repository_support import ensure_profile_exists
from afkbot.services.automations.update_runtime import apply_automation_update
from afkbot.services.automations.validators import (
    compute_next_run_at,
    normalize_cron_expr,
    normalize_automation_prompt,
    normalize_execution_mode,
    normalize_graph_fallback_mode,
    normalize_timezone_name,
    normalize_update_status,
    validate_create_payload,
)
from afkbot.services.automations.webhook_secrets import (
    encrypt_webhook_token,
    recover_webhook_token,
)
from afkbot.services.automations.webhook_tokens import (
    build_webhook_path,
    build_webhook_url,
    hash_webhook_token,
    is_webhook_token_conflict,
    issue_webhook_token,
    mask_webhook_token,
)
from afkbot.services.automations.webhook_execution import trigger_webhook_automation
from afkbot.services.automations.session_runner_factory import AutomationSessionRunnerFactory
from afkbot.settings import Settings, get_settings

_SERVICES_BY_ROOT: dict[str, "AutomationsService"] = {}
TValue = TypeVar("TValue")
_WEBHOOK_TOKEN_ISSUE_ATTEMPTS = 5
_DEFAULT_AUTOMATION_RUN_TIMEOUT_SEC = 1800.0

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
        graph_subagent_service_factory: AutomationGraphSubagentFactory | None = None,
    ) -> None:
        """Capture storage/session dependencies for automation operations."""

        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine
        self._graph_service = AutomationGraphService(
            session_factory=session_factory,
            settings=self._resolve_execution_settings(),
            subagent_service_factory=graph_subagent_service_factory,
        )

    async def create_cron(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
        cron_expr: str,
        timezone_name: str = "UTC",
        execution_mode: str = "prompt",
        graph_fallback_mode: str = "resume_with_ai_if_safe",
    ) -> AutomationMetadata:
        """Create profile automation with cron trigger metadata."""

        validate_create_payload(name=name, prompt=prompt)
        normalized_cron = normalize_cron_expr(cron_expr)
        normalized_timezone = normalize_timezone_name(timezone_name)
        normalized_prompt = normalize_automation_prompt(prompt)
        normalized_execution_mode = normalize_execution_mode(execution_mode)
        normalized_graph_fallback_mode = normalize_graph_fallback_mode(graph_fallback_mode)
        now_utc = datetime.now(timezone.utc)
        next_run_at = compute_next_run_at(normalized_cron, now_utc, normalized_timezone)

        async def _op(repo: AutomationRepository) -> AutomationMetadata:
            await ensure_profile_exists(repo, profile_id)
            automation, cron = await repo.create_cron_automation(
                profile_id=profile_id,
                name=name.strip(),
                prompt=normalized_prompt,
                cron_expr=normalized_cron,
                timezone=normalized_timezone,
                next_run_at=next_run_at,
                execution_mode=normalized_execution_mode,
                graph_fallback_mode=normalized_graph_fallback_mode,
                delivery_mode="tool",
            )
            return self._to_metadata(automation=automation, cron=cron, webhook=None)

        return await self._with_repo(_op)

    async def create_webhook(
        self,
        *,
        profile_id: str,
        name: str,
        prompt: str,
        execution_mode: str = "prompt",
        graph_fallback_mode: str = "resume_with_ai_if_safe",
    ) -> AutomationMetadata:
        """Create profile automation with generated webhook trigger token."""

        validate_create_payload(name=name, prompt=prompt)
        stripped_name = name.strip()
        normalized_prompt = normalize_automation_prompt(prompt)
        normalized_execution_mode = normalize_execution_mode(execution_mode)
        normalized_graph_fallback_mode = normalize_graph_fallback_mode(graph_fallback_mode)
        for attempt in range(_WEBHOOK_TOKEN_ISSUE_ATTEMPTS):
            token = issue_webhook_token()
            token_hash = hash_webhook_token(token)
            encrypted_webhook_token, webhook_token_key_version = encrypt_webhook_token(
                plaintext_token=token,
                settings=self._settings,
            )

            async def _op(repo: AutomationRepository) -> AutomationMetadata:
                await ensure_profile_exists(repo, profile_id)
                automation, webhook = await repo.create_webhook_automation(
                    profile_id=profile_id,
                    name=stripped_name,
                    prompt=normalized_prompt,
                    webhook_token_hash=token_hash,
                    encrypted_webhook_token=encrypted_webhook_token,
                    webhook_token_key_version=webhook_token_key_version,
                    execution_mode=normalized_execution_mode,
                    graph_fallback_mode=normalized_graph_fallback_mode,
                    delivery_mode="tool",
                )
                return self._to_metadata(
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
            return self._to_metadata(automation=row[0], cron=row[1], webhook=row[2])

        return await self._with_repo(_op)

    async def reveal_webhook_endpoint(
        self,
        *,
        profile_id: str,
        automation_id: int,
    ) -> AutomationWebhookEndpointMetadata:
        """Reveal the current webhook endpoint for operator-facing surfaces only."""

        async def _op(repo: AutomationRepository) -> AutomationWebhookEndpointMetadata:
            await ensure_profile_exists(repo, profile_id)
            row = await repo.get_by_id(profile_id=profile_id, automation_id=automation_id)
            if row is None or row[0].status == "deleted":
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            automation, _, webhook = row
            if automation.trigger_type != "webhook" or webhook is None:
                raise AutomationsServiceError(
                    error_code="invalid_trigger_type",
                    reason="Automation does not use a webhook trigger",
                )
            persisted_webhook_token = recover_webhook_token(
                webhook=webhook,
                settings=self._settings,
            )
            return AutomationWebhookEndpointMetadata(
                recoverable=persisted_webhook_token is not None,
                webhook_path=build_webhook_path(automation.profile_id, persisted_webhook_token),
                webhook_url=build_webhook_url(
                    self._resolve_runtime_base_url(),
                    automation.profile_id,
                    persisted_webhook_token,
                ),
                webhook_token_masked=mask_webhook_token(persisted_webhook_token),
            )

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
                self._to_metadata(automation=automation, cron=cron, webhook=webhook)
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
        execution_mode: str | None = None,
        graph_fallback_mode: str | None = None,
        cron_expr: str | None = None,
        timezone_name: str | None = None,
        rotate_webhook_token: bool | None = None,
    ) -> AutomationMetadata:
        """Update one profile automation and trigger-specific fields."""
        has_base_updates = any(
            field is not None
            for field in (name, prompt, status, execution_mode, graph_fallback_mode)
        )
        has_cron_updates = cron_expr is not None or timezone_name is not None
        should_rotate_webhook_token = bool(rotate_webhook_token)
        if not has_base_updates and not has_cron_updates and not should_rotate_webhook_token:
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
        normalized_execution_mode = (
            normalize_execution_mode(execution_mode) if execution_mode is not None else None
        )
        normalized_graph_fallback_mode = (
            normalize_graph_fallback_mode(graph_fallback_mode)
            if graph_fallback_mode is not None
            else None
        )
        normalized_cron = normalize_cron_expr(cron_expr) if cron_expr is not None else None
        normalized_timezone = (
            normalize_timezone_name(timezone_name) if timezone_name is not None else None
        )

        async def _run_update(issued_webhook_token: str | None) -> AutomationMetadata:
            encrypted_webhook_token, webhook_token_key_version = (
                encrypt_webhook_token(
                    plaintext_token=issued_webhook_token,
                    settings=self._settings,
                )
                if issued_webhook_token is not None
                else (None, None)
            )
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
                normalized_execution_mode=normalized_execution_mode,
                normalized_graph_fallback_mode=normalized_graph_fallback_mode,
                normalized_cron=normalized_cron,
                normalized_timezone=normalized_timezone,
                issued_webhook_token=issued_webhook_token,
                encrypted_webhook_token=encrypted_webhook_token,
                webhook_token_key_version=webhook_token_key_version,
                to_metadata=self._to_metadata,
                compute_next_run_at=compute_next_run_at,
                hash_webhook_token=hash_webhook_token,
            )

        if not should_rotate_webhook_token:
            return await _run_update(None)

        for attempt in range(_WEBHOOK_TOKEN_ISSUE_ATTEMPTS):
            issued_webhook_token = issue_webhook_token()
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
        profile_id: str,
        token: str,
        payload: Mapping[str, object],
        session_runner_factory: AutomationSessionRunnerFactory | None = None,
    ) -> AutomationWebhookTriggerResult:
        """Trigger one webhook automation and run its prompt through session orchestration."""

        return await trigger_webhook_automation(
            session_factory=self._session_factory,
            with_repo=self._with_repo,
            profile_id=profile_id,
            token=token,
            payload=payload,
            settings=self._resolve_execution_settings(),
            session_runner_factory=session_runner_factory,
            graph_service=self._graph_service,
            run_timeout_sec=self._resolve_automation_run_timeout_sec(),
        )

    async def tick_cron(
        self,
        *,
        now_utc: datetime,
        session_runner_factory: AutomationSessionRunnerFactory | None = None,
        max_due_per_tick: int | None = None,
    ) -> AutomationCronTickResult:
        """Run due cron automations and update their next execution timestamp."""

        return await tick_cron_automations(
            session_factory=self._session_factory,
            with_repo=self._with_repo,
            now_utc=now_utc,
            settings=self._resolve_execution_settings(),
            session_runner_factory=session_runner_factory,
            graph_service=self._graph_service,
            compute_next_run_at=compute_next_run_at,
            max_due_per_tick=max_due_per_tick,
            run_timeout_sec=self._resolve_automation_run_timeout_sec(),
        )

    async def apply_graph(
        self,
        *,
        profile_id: str,
        automation_id: int,
        spec: AutomationGraphSpec,
    ) -> AutomationGraphMetadata:
        """Replace the active graph definition for one automation."""

        return await self._graph_service.apply_graph(
            profile_id=profile_id,
            automation_id=automation_id,
            spec=spec,
        )

    async def get_graph(
        self,
        *,
        profile_id: str,
        automation_id: int,
    ) -> AutomationGraphMetadata:
        """Return the active graph snapshot for one automation."""

        return await self._graph_service.get_graph(
            profile_id=profile_id,
            automation_id=automation_id,
        )

    async def validate_graph(
        self,
        *,
        profile_id: str,
        automation_id: int,
    ) -> AutomationGraphValidationReport:
        """Validate the active graph for one automation."""

        return await self._graph_service.validate_graph(
            profile_id=profile_id,
            automation_id=automation_id,
        )

    async def list_graph_runs(
        self,
        *,
        profile_id: str,
        automation_id: int,
        limit: int = 20,
    ) -> builtins.list[AutomationGraphRunMetadata]:
        """List recent graph runs for one automation."""

        return await self._graph_service.list_runs(
            profile_id=profile_id,
            automation_id=automation_id,
            limit=limit,
        )

    async def get_graph_run(
        self,
        *,
        profile_id: str,
        run_id: int,
    ) -> AutomationGraphRunMetadata:
        """Return one graph run metadata record."""

        return await self._graph_service.get_run(profile_id=profile_id, run_id=run_id)

    async def get_graph_trace(
        self,
        *,
        profile_id: str,
        run_id: int,
    ) -> AutomationGraphTraceMetadata:
        """Return one graph run trace payload."""

        return await self._graph_service.get_trace(profile_id=profile_id, run_id=run_id)

    async def _with_repo(
        self,
        op: Callable[[AutomationRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = AutomationRepository(session)
            return await op(repo)

    def _to_metadata(
        self,
        *,
        automation: Automation,
        cron: AutomationTriggerCron | None,
        webhook: AutomationTriggerWebhook | None,
        issued_webhook_token: str | None = None,
    ) -> AutomationMetadata:
        """Map automation rows into public metadata using current runtime base URL settings."""

        return to_metadata(
            automation=automation,
            cron=cron,
            webhook=webhook,
            runtime_base_url=self._resolve_runtime_base_url(),
            issued_webhook_token=issued_webhook_token,
        )

    def _resolve_runtime_base_url(self) -> str | None:
        """Return best-effort base URL for absolute webhook URL rendering."""

        if self._settings is None:
            return None
        public_runtime_url = (self._settings.public_runtime_url or "").strip()
        if public_runtime_url:
            return public_runtime_url.rstrip("/")
        host = self._settings.runtime_host.strip()
        if not host or host in {"0.0.0.0", "::", "*"}:
            return None
        return f"http://{host}:{int(self._settings.runtime_port)}"

    def _resolve_automation_run_timeout_sec(self) -> float:
        """Return the hard wall-clock timeout for one automation run."""

        if self._settings is None:
            return _DEFAULT_AUTOMATION_RUN_TIMEOUT_SEC
        return max(0.001, float(self._settings.automation_run_timeout_sec))

    def _resolve_execution_settings(self) -> Settings:
        """Return concrete runtime settings for automation turn execution."""

        return self._settings or get_settings()

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
