"""Detached execution runtime for AI-owned Task Flow tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import secrets
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.task_flow.lease_runtime import run_with_lease_refresh
from afkbot.services.task_flow.message_factory import compose_task_message, task_session_id
from afkbot.services.task_flow.runtime_target import build_task_flow_runtime_target
from afkbot.settings import Settings, get_settings

if TYPE_CHECKING:
    from afkbot.services.agent_loop.action_contracts import TurnResult
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides
    from afkbot.services.agent_loop.loop import AgentLoop

_LOGGER = logging.getLogger(__name__)
_RUNTIME_UNSET = object()


class AgentLoopLike(Protocol):
    """Minimal AgentLoop contract used by Task Flow runtime execution."""

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult: ...


@dataclass(frozen=True, slots=True)
class ClaimedTaskExecution:
    """Normalized claimed task state used by the detached worker runtime."""

    task_id: str
    task_profile_id: str
    execution_profile_id: str
    flow_id: str | None
    prompt: str
    owner_type: str
    owner_ref: str
    source_type: str
    source_ref: str | None
    priority: int
    attempt: int
    requires_review: bool
    labels: tuple[str, ...]
    claim_token: str
    task_run_id: int
    session_id: str


@dataclass(frozen=True, slots=True)
class TaskExecutionOutcome:
    """Final persisted outcome for one detached task attempt."""

    status: str
    summary: str | None = None
    error_code: str | None = None
    error_text: str | None = None
    blocked_reason_code: str | None = None
    blocked_reason_text: str | None = None
    run_id: int | None = None


class TaskFlowRuntimeService:
    """Claim, execute, and finalize background Task Flow work items."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        engine: AsyncEngine | None = None,
        agent_loop_factory: Callable[[AsyncSession, str], AgentLoopLike] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._engine = engine
        self._managed_engine: AsyncEngine | None = None
        self._start_lock = asyncio.Lock()
        self._agent_loop_factory = agent_loop_factory or _default_agent_loop_factory(
            self._settings
        )

    async def start(self) -> None:
        """Prepare storage resources when the runtime owns them."""

        await self._ensure_started()

    async def shutdown(self) -> None:
        """Dispose owned async engine when the runtime created it."""

        if self._managed_engine is None:
            return
        await self._managed_engine.dispose()
        self._managed_engine = None
        self._session_factory = None
        if self._engine is None:
            return
        self._engine = None

    async def execute_next_claimable_task(self, *, worker_id: str) -> bool:
        """Claim and execute one runnable AI-owned task, returning whether work was found."""

        await self._ensure_started()
        claimed = await self._claim_next_task(worker_id=worker_id)
        if claimed is None:
            return False
        await self._execute_claimed_task(claimed)
        return True

    async def _ensure_started(self) -> None:
        if self._session_factory is not None:
            return
        async with self._start_lock:
            if self._session_factory is not None:
                return
            if self._engine is not None:
                await create_schema(self._engine)
                self._session_factory = create_session_factory(self._engine)
                return
            engine = create_engine(self._settings)
            await create_schema(engine)
            self._managed_engine = engine
            self._session_factory = create_session_factory(engine)

    async def _claim_next_task(self, *, worker_id: str) -> ClaimedTaskExecution | None:
        session_factory = self._require_session_factory()
        now_utc = datetime.now(timezone.utc)
        claim_ttl = _claim_ttl(self._settings)
        claim_token = secrets.token_hex(16)
        async with session_scope(session_factory) as session:
            repo = TaskFlowRepository(session)
            row = await repo.claim_next_runnable_task(
                now_utc=now_utc,
                lease_until=now_utc + claim_ttl,
                claim_token=claim_token,
                claimed_by=worker_id,
            )
            if row is None:
                return None
            session_id = task_session_id(task_id=row.id)
            task_run = await repo.create_task_run(
                task_id=row.id,
                attempt=row.current_attempt,
                owner_type=row.owner_type,
                owner_ref=row.owner_ref,
                execution_mode="detached",
                status="claimed",
                session_id=session_id,
                run_id=None,
                worker_id=worker_id,
                started_at=now_utc,
            )
            attached = await repo.attach_task_run(
                task_id=row.id,
                claim_token=claim_token,
                task_run_id=task_run.id,
                session_id=session_id,
            )
            if not attached:
                raise RuntimeError("Failed to attach claimed task run metadata")
            return ClaimedTaskExecution(
                task_id=row.id,
                task_profile_id=row.profile_id,
                execution_profile_id=_resolve_execution_profile_id(row),
                flow_id=row.flow_id,
                prompt=row.prompt,
                owner_type=row.owner_type,
                owner_ref=row.owner_ref,
                source_type=row.source_type,
                source_ref=row.source_ref,
                priority=row.priority,
                attempt=row.current_attempt,
                requires_review=bool(row.requires_review),
                labels=tuple(_decode_labels(row.labels_json)),
                claim_token=claim_token,
                task_run_id=task_run.id,
                session_id=session_id,
            )

    async def _execute_claimed_task(self, claimed: ClaimedTaskExecution) -> None:
        started = await self._mark_started(claimed=claimed)
        if not started:
            await self._release_for_retry(
                claimed=claimed,
                error_code="task_claim_lost",
                error_text="Failed to transition claimed task into running state",
            )
            return
        runtime_target = build_task_flow_runtime_target(
            execution_profile_id=claimed.execution_profile_id,
            session_id=claimed.session_id,
            task_id=claimed.task_id,
            task_profile_id=claimed.task_profile_id,
            owner_type=claimed.owner_type,
            owner_ref=claimed.owner_ref,
            flow_id=claimed.flow_id,
            source_type=claimed.source_type,
            source_ref=claimed.source_ref,
            priority=claimed.priority,
            attempt=claimed.attempt,
            requires_review=claimed.requires_review,
            labels=claimed.labels,
        )
        message = compose_task_message(claimed.prompt)
        claim_ttl = _claim_ttl(self._settings)

        async def _run() -> TurnResult:
            session_factory = self._require_session_factory()
            async with session_scope(session_factory) as session:
                loop = self._agent_loop_factory(session, claimed.execution_profile_id)
                result = await loop.run_turn(
                    profile_id=runtime_target.profile_id,
                    session_id=runtime_target.session_id,
                    message=message,
                    context_overrides=runtime_target.context_overrides,
                )
                return result

        async def _refresh() -> bool:
            return await self._refresh_claim(claimed=claimed)

        try:
            result = await run_with_lease_refresh(
                run=_run,
                refresh=_refresh,
                ttl=claim_ttl,
            )
            outcome = await self._classify_turn_result(
                claimed=claimed,
                result=result,
            )
            await self._persist_outcome(claimed=claimed, outcome=outcome)
        except asyncio.CancelledError:
            await self._release_for_retry(
                claimed=claimed,
                error_code="task_execution_cancelled",
                error_text="Task execution cancelled before completion",
            )
            raise
        except Exception as exc:
            error_code, error_text = _format_runtime_exception(exc)
            await self._persist_failure(
                claimed=claimed,
                error_code=error_code,
                error_text=error_text,
            )

    async def _mark_started(self, *, claimed: ClaimedTaskExecution) -> bool:
        session_factory = self._require_session_factory()
        started_at = datetime.now(timezone.utc)
        async with session_scope(session_factory) as session:
            repo = TaskFlowRepository(session)
            started = await repo.mark_task_started(
                task_id=claimed.task_id,
                claim_token=claimed.claim_token,
                started_at=started_at,
            )
            if started:
                await repo.update_task_run(
                    task_run_id=claimed.task_run_id,
                    status="running",
                )
            return started

    async def _refresh_claim(self, *, claimed: ClaimedTaskExecution) -> bool:
        session_factory = self._require_session_factory()
        async with session_scope(session_factory) as session:
            repo = TaskFlowRepository(session)
            return await repo.refresh_task_claim(
                task_id=claimed.task_id,
                claim_token=claimed.claim_token,
                lease_until=datetime.now(timezone.utc) + _claim_ttl(self._settings),
            )

    async def _persist_outcome(
        self,
        *,
        claimed: ClaimedTaskExecution,
        outcome: TaskExecutionOutcome,
    ) -> None:
        session_factory = self._require_session_factory()
        finished_at = datetime.now(timezone.utc)
        reconcile_completed = False
        async with session_scope(session_factory) as session:
            repo = TaskFlowRepository(session)
            finalized = await repo.finalize_task_claim(
                task_id=claimed.task_id,
                claim_token=claimed.claim_token,
                status=outcome.status,
                finished_at=finished_at,
                last_run_id=claimed.task_run_id,
                last_error_code=outcome.error_code if outcome.status == "failed" else None,
                last_error_text=outcome.error_text if outcome.status == "failed" else None,
                blocked_reason_code=outcome.blocked_reason_code,
                blocked_reason_text=outcome.blocked_reason_text,
            )
            if finalized:
                await repo.update_task_run(
                    task_run_id=claimed.task_run_id,
                    status=outcome.status,
                    run_id=outcome.run_id,
                    summary=outcome.summary,
                    error_code=outcome.error_code,
                    error_text=outcome.error_text,
                    finished_at=finished_at,
                )
                reconcile_completed = outcome.status == "completed"
            else:
                reconcile_completed = await self._handle_unfinalized_outcome(
                    repo=repo,
                    claimed=claimed,
                    outcome=outcome,
                    finished_at=finished_at,
                )
        if reconcile_completed:
            from afkbot.services.task_flow.service import TaskFlowService

            service = TaskFlowService(self._require_session_factory())
            await service.reconcile_completed_task(
                profile_id=claimed.task_profile_id,
                task_id=claimed.task_id,
            )

    async def _persist_failure(
        self,
        *,
        claimed: ClaimedTaskExecution,
        error_code: str,
        error_text: str,
    ) -> None:
        await self._persist_outcome(
            claimed=claimed,
            outcome=TaskExecutionOutcome(
                status="failed",
                error_code=error_code,
                error_text=error_text,
                blocked_reason_code=None,
                blocked_reason_text=None,
            ),
        )

    async def _release_for_retry(
        self,
        *,
        claimed: ClaimedTaskExecution,
        error_code: str,
        error_text: str,
    ) -> None:
        session_factory = self._require_session_factory()
        finished_at = datetime.now(timezone.utc)
        async with session_scope(session_factory) as session:
            repo = TaskFlowRepository(session)
            await repo.release_task_claim(
                task_id=claimed.task_id,
                claim_token=claimed.claim_token,
                ready_at=finished_at,
                error_code=error_code,
                error_text=error_text,
            )
            await repo.update_task_run(
                task_run_id=claimed.task_run_id,
                status="cancelled",
                error_code=error_code,
                error_text=error_text,
                finished_at=finished_at,
            )

    async def _mark_run_cancelled(
        self,
        *,
        claimed: ClaimedTaskExecution,
        error_code: str,
        error_text: str,
    ) -> None:
        session_factory = self._require_session_factory()
        finished_at = datetime.now(timezone.utc)
        async with session_scope(session_factory) as session:
            repo = TaskFlowRepository(session)
            await repo.update_task_run(
                task_run_id=claimed.task_run_id,
                status="cancelled",
                error_code=error_code,
                error_text=error_text,
                finished_at=finished_at,
            )

    async def _handle_unfinalized_outcome(
        self,
        *,
        repo: TaskFlowRepository,
        claimed: ClaimedTaskExecution,
        outcome: TaskExecutionOutcome,
        finished_at: datetime,
    ) -> bool:
        """Resolve races where task state changed before this worker finalized its claim."""

        current = await repo.get_task(
            profile_id=claimed.task_profile_id,
            task_id=claimed.task_id,
        )
        if current is not None and current.claim_token == claimed.claim_token:
            await repo.clear_task_claim(
                task_id=claimed.task_id,
                claim_token=claimed.claim_token,
                last_run_id=claimed.task_run_id,
                last_error_code=(
                    outcome.error_code
                    if outcome.status == "failed"
                    else _RUNTIME_UNSET
                ),
                last_error_text=(
                    outcome.error_text
                    if outcome.status == "failed"
                    else _RUNTIME_UNSET
                ),
            )
            current = await repo.get_task(
                profile_id=claimed.task_profile_id,
                task_id=claimed.task_id,
            )

        if current is None:
            await repo.update_task_run(
                task_run_id=claimed.task_run_id,
                status="cancelled",
                run_id=outcome.run_id,
                summary=outcome.summary,
                error_code="task_not_found",
                error_text="Task disappeared before runtime finalization completed.",
                finished_at=finished_at,
            )
            return False

        persisted_status = str(current.status or "").strip().lower()
        if persisted_status in {"claimed", "running"}:
            await repo.update_task_run(
                task_run_id=claimed.task_run_id,
                status="cancelled",
                run_id=outcome.run_id,
                summary=outcome.summary,
                error_code="task_claim_lost",
                error_text="Task claim was lost before runtime finalization completed.",
                finished_at=finished_at,
            )
            return False

        task_run_status = _task_run_status_for_persisted_task(current=current, claimed=claimed)
        await repo.update_task_run(
            task_run_id=claimed.task_run_id,
            status=task_run_status,
            run_id=outcome.run_id,
            summary=(
                _trim_text(current.blocked_reason_text, limit=4000)
                if persisted_status in {"blocked", "review"}
                else outcome.summary
            ),
            error_code=_task_run_error_code(current=current, outcome=outcome),
            error_text=_task_run_error_text(current=current, outcome=outcome),
            finished_at=finished_at,
        )
        if persisted_status == "completed":
            _LOGGER.info(
                "taskflow_runtime_observed_external_completion task_id=%s task_run_id=%s",
                claimed.task_id,
                claimed.task_run_id,
            )
            return True
        return False

    async def _classify_turn_result(
        self,
        *,
        claimed: ClaimedTaskExecution,
        result: TurnResult,
    ) -> TaskExecutionOutcome:
        envelope = result.envelope
        if envelope.action != "finalize":
            code = envelope.blocked_reason or f"task_action_{envelope.action}"
            text = _trim_text(envelope.message, limit=2000)
            return TaskExecutionOutcome(
                status="blocked",
                summary=text,
                error_code=code,
                error_text=text,
                blocked_reason_code=code,
                blocked_reason_text=text,
                run_id=result.run_id,
            )

        runlog_failure = await self._classify_runlog_failure(run_id=result.run_id)
        if runlog_failure is not None:
            return runlog_failure

        if envelope.blocked_reason:
            text = _trim_text(envelope.message, limit=2000)
            return TaskExecutionOutcome(
                status="blocked",
                summary=text,
                error_code=envelope.blocked_reason,
                error_text=text,
                blocked_reason_code=envelope.blocked_reason,
                blocked_reason_text=text,
                run_id=result.run_id,
            )

        return TaskExecutionOutcome(
            status="review" if claimed.requires_review else "completed",
            summary=_trim_text(envelope.message, limit=4000),
            run_id=result.run_id,
        )

    async def _classify_runlog_failure(self, *, run_id: int) -> TaskExecutionOutcome | None:
        session_factory = self._require_session_factory()
        async with session_scope(session_factory) as session:
            events = await RunlogRepository(session).list_run_events_since(
                run_id=run_id,
                after_event_id=0,
                limit=256,
            )
        for event in events:
            payload = _load_payload(event.payload_json)
            if event.event_type == "turn.finalize":
                blocked_reason = str(payload.get("blocked_reason") or "").strip()
                if blocked_reason:
                    message = _trim_text(
                        str(payload.get("assistant_message") or blocked_reason),
                        limit=2000,
                    )
                    return TaskExecutionOutcome(
                        status="blocked",
                        summary=message,
                        error_code=blocked_reason,
                        error_text=message,
                        blocked_reason_code=blocked_reason,
                        blocked_reason_text=message,
                        run_id=run_id,
                    )
                continue
            if event.event_type == "llm.call.timeout":
                error_code = str(payload.get("error_code") or "llm_timeout").strip()
                return TaskExecutionOutcome(
                    status="failed",
                    error_code=error_code,
                    error_text="Task run timed out while waiting for the LLM provider.",
                    run_id=run_id,
                )
            if event.event_type == "llm.call.error":
                error_code = str(payload.get("error_code") or "llm_provider_error").strip()
                reason = str(payload.get("reason") or "").strip() or (
                    "Task run failed before the LLM provider completed."
                )
                return TaskExecutionOutcome(
                    status="failed",
                    error_code=error_code,
                    error_text=_trim_text(reason, limit=2000),
                    run_id=run_id,
                )
            if event.event_type == "llm.call.done":
                error_code = str(payload.get("error_code") or "").strip()
                if error_code:
                    reason = str(payload.get("reason") or "").strip() or (
                        f"Task run completed with LLM error code: {error_code}"
                    )
                    return TaskExecutionOutcome(
                        status="failed",
                        error_code=error_code,
                        error_text=_trim_text(reason, limit=2000),
                        run_id=run_id,
                    )
        return None

    def _require_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Task Flow runtime service is not started")
        return self._session_factory


def build_taskflow_runtime_agent_loop(
    session: AsyncSession,
    *,
    profile_id: str,
    settings: Settings | None = None,
) -> "AgentLoop":
    """Build AgentLoop instance used by Task Flow detached runtime execution."""

    from afkbot.services.agent_loop.runtime_factory import build_profile_agent_loop

    effective_settings = settings or get_settings()
    return build_profile_agent_loop(
        session,
        settings=effective_settings,
        profile_id=profile_id,
    )


def _default_agent_loop_factory(
    settings: Settings,
) -> Callable[[AsyncSession, str], AgentLoopLike]:
    return lambda session, profile_id: build_taskflow_runtime_agent_loop(
        session,
        profile_id=profile_id,
        settings=settings,
    )


def _resolve_execution_profile_id(row: object) -> str:
    owner_type = str(getattr(row, "owner_type", "") or "").strip().lower()
    owner_ref = str(getattr(row, "owner_ref", "") or "").strip()
    task_profile_id = str(getattr(row, "profile_id", "") or "").strip()
    if owner_type == "ai_profile" and owner_ref:
        return owner_ref
    return task_profile_id


def _claim_ttl(settings: Settings) -> timedelta:
    return timedelta(seconds=max(1, int(settings.taskflow_runtime_claim_ttl_sec)))


def _load_payload(payload_json: str) -> dict[str, object]:
    try:
        raw = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _decode_labels(raw_json: str) -> list[str]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def _trim_text(value: str | None, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _format_runtime_exception(exc: BaseException) -> tuple[str, str]:
    error_code = str(getattr(exc, "error_code", "") or "").strip()
    if not error_code:
        error_code = type(exc).__name__.lower()[:64]
    reason = str(getattr(exc, "reason", "") or "").strip()
    if not reason:
        reason = str(exc).strip()
    if not reason:
        reason = type(exc).__name__
    return error_code[:64], reason[:2000]


def _task_run_status_for_persisted_task(
    *,
    current: object,
    claimed: ClaimedTaskExecution,
) -> str:
    status = str(getattr(current, "status", "") or "").strip().lower()
    if status == "todo":
        owner_type = str(getattr(current, "owner_type", "") or "").strip().lower()
        owner_ref = str(getattr(current, "owner_ref", "") or "").strip()
        if owner_type != claimed.owner_type or owner_ref != claimed.owner_ref:
            return "reassigned"
        return "cancelled"
    return status or "cancelled"


def _task_run_error_code(*, current: object, outcome: TaskExecutionOutcome) -> str | None:
    blocked_reason_code = str(getattr(current, "blocked_reason_code", "") or "").strip()
    if blocked_reason_code:
        return blocked_reason_code[:64]
    last_error_code = str(getattr(current, "last_error_code", "") or "").strip()
    if last_error_code:
        return last_error_code[:64]
    return outcome.error_code


def _task_run_error_text(*, current: object, outcome: TaskExecutionOutcome) -> str | None:
    blocked_reason_text = _trim_text(getattr(current, "blocked_reason_text", None), limit=2000)
    if blocked_reason_text:
        return blocked_reason_text
    last_error_text = _trim_text(getattr(current, "last_error_text", None), limit=2000)
    if last_error_text:
        return last_error_text
    return outcome.error_text
