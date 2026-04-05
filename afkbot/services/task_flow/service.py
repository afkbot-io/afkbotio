"""Task Flow service orchestration over repository and storage helpers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import TypeVar
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.task import Task
from afkbot.models.task_dependency import TaskDependency
from afkbot.models.task_flow import TaskFlow
from afkbot.models.task_run import TaskRun
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.repositories.support import profile_exists
from afkbot.services.task_flow.contracts import (
    HumanTaskStartupSummary,
    TaskBoardColumnMetadata,
    TaskBoardMetadata,
    TaskDependencyMetadata,
    TaskFlowMetadata,
    TaskMetadata,
    TaskRunMetadata,
)
from afkbot.services.task_flow.errors import TaskFlowServiceError
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "TaskFlowService"] = {}
_VALID_OWNER_TYPES = {"ai_profile", "human"}
_VALID_TASK_STATUSES = {
    "todo",
    "claimed",
    "running",
    "blocked",
    "review",
    "completed",
    "failed",
    "cancelled",
}
_VALID_FLOW_STATUSES = {"active", "completed", "cancelled", "archived"}
_VISIBLE_HUMAN_STATUSES = ("todo", "blocked", "review")
_TASK_BOARD_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("todo", "Todo", ("todo",)),
    ("blocked", "Blocked", ("blocked",)),
    ("running", "Running", ("claimed", "running")),
    ("review", "Review", ("review",)),
    ("completed", "Completed", ("completed",)),
    ("failed", "Failed", ("failed",)),
    ("cancelled", "Cancelled", ("cancelled",)),
)
TValue = TypeVar("TValue")


class TaskFlowService:
    """Service for task flow CRUD and human startup summaries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine

    async def create_flow(
        self,
        *,
        profile_id: str,
        title: str,
        description: str | None = None,
        created_by_type: str,
        created_by_ref: str,
        default_owner_type: str | None = None,
        default_owner_ref: str | None = None,
        labels: Sequence[str] = (),
    ) -> TaskFlowMetadata:
        """Create one task flow container."""

        normalized_title = _normalize_required_text(title, field_name="title")
        _validate_owner_pair(
            owner_type=default_owner_type,
            owner_ref=default_owner_ref,
            allow_missing=True,
        )
        normalized_labels = _normalize_labels(labels)

        async def _op(repo: TaskFlowRepository) -> TaskFlowMetadata:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.create_flow(
                flow_id=_new_identifier("flow"),
                profile_id=profile_id,
                title=normalized_title,
                description=_normalize_optional_text(description),
                status="active",
                created_by_type=_normalize_required_text(created_by_type, field_name="created_by_type"),
                created_by_ref=_normalize_required_text(created_by_ref, field_name="created_by_ref"),
                default_owner_type=_normalize_optional_text(default_owner_type),
                default_owner_ref=_normalize_optional_text(default_owner_ref),
                labels_json=json.dumps(normalized_labels),
            )
            return _to_flow_metadata(row)

        return await self._with_repo(_op)

    async def list_flows(self, *, profile_id: str) -> list[TaskFlowMetadata]:
        """List flows for one profile."""

        async def _op(repo: TaskFlowRepository) -> list[TaskFlowMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            rows = await repo.list_flows(profile_id=profile_id)
            return [_to_flow_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def get_flow(self, *, profile_id: str, flow_id: str) -> TaskFlowMetadata:
        """Get one flow by id."""

        async def _op(repo: TaskFlowRepository) -> TaskFlowMetadata:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_flow(profile_id=profile_id, flow_id=flow_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_flow_not_found", reason="Task flow not found")
            return _to_flow_metadata(row)

        return await self._with_repo(_op)

    async def create_task(
        self,
        *,
        profile_id: str,
        title: str,
        prompt: str,
        created_by_type: str,
        created_by_ref: str,
        flow_id: str | None = None,
        priority: int = 50,
        due_at: datetime | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reviewer_type: str | None = None,
        reviewer_ref: str | None = None,
        source_type: str = "manual",
        source_ref: str | None = None,
        labels: Sequence[str] = (),
        requires_review: bool = False,
        depends_on_task_ids: Sequence[str] = (),
    ) -> TaskMetadata:
        """Create one task and optional dependency edges."""

        normalized_title = _normalize_required_text(title, field_name="title")
        normalized_prompt = _normalize_required_text(prompt, field_name="prompt")
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_labels = _normalize_labels(labels)
        normalized_depends_on = _normalize_identifier_list(depends_on_task_ids)
        normalized_source_type = _normalize_required_text(source_type, field_name="source_type")

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            await _ensure_profile_exists(repo, profile_id)
            flow = None
            if normalized_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )

            resolved_owner_type = _normalize_optional_text(owner_type) or _normalize_optional_text(
                flow.default_owner_type if flow is not None else None
            )
            resolved_owner_ref = _normalize_optional_text(owner_ref) or _normalize_optional_text(
                flow.default_owner_ref if flow is not None else None
            )
            if resolved_owner_type is None:
                resolved_owner_type = "ai_profile"
            if resolved_owner_ref is None:
                resolved_owner_ref = profile_id
            _validate_owner_pair(owner_type=resolved_owner_type, owner_ref=resolved_owner_ref)
            _validate_owner_pair(
                owner_type=reviewer_type,
                owner_ref=reviewer_ref,
                allow_missing=True,
            )
            await _ensure_actor_refs_exist(
                repo,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=_normalize_optional_text(reviewer_type),
                reviewer_ref=_normalize_optional_text(reviewer_ref),
            )

            status = "blocked" if normalized_depends_on else "todo"
            blocked_reason_code = "dependency_wait" if normalized_depends_on else None
            blocked_reason_text = (
                "Waiting for dependent tasks to complete." if normalized_depends_on else None
            )
            now_utc = datetime.now(timezone.utc)
            row = await repo.create_task(
                task_id=_new_identifier("task"),
                profile_id=profile_id,
                flow_id=normalized_flow_id,
                title=normalized_title,
                prompt=normalized_prompt,
                status=status,
                priority=priority,
                due_at=due_at,
                ready_at=None if normalized_depends_on else now_utc,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=_normalize_optional_text(reviewer_type),
                reviewer_ref=_normalize_optional_text(reviewer_ref),
                source_type=normalized_source_type,
                source_ref=_normalize_optional_text(source_ref),
                created_by_type=_normalize_required_text(created_by_type, field_name="created_by_type"),
                created_by_ref=_normalize_required_text(created_by_ref, field_name="created_by_ref"),
                labels_json=json.dumps(normalized_labels),
                requires_review=bool(requires_review),
                blocked_reason_code=blocked_reason_code,
                blocked_reason_text=blocked_reason_text,
            )
            for dependency_task_id in normalized_depends_on:
                await _create_dependency_edge(
                    repo=repo,
                    profile_id=profile_id,
                    task_id=row.id,
                    depends_on_task_id=dependency_task_id,
                    satisfied_on_status="completed",
                )
            if normalized_depends_on:
                row = await _reconcile_task_readiness(repo=repo, task=row)
            return await _build_task_metadata(repo, row)

        return await self._with_repo(_op)

    async def get_task(self, *, profile_id: str, task_id: str) -> TaskMetadata:
        """Get one task by id."""

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            return await _build_task_metadata(repo, row)

        return await self._with_repo(_op)

    async def build_board(
        self,
        *,
        profile_id: str,
        flow_id: str | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        labels: Sequence[str] = (),
        limit_per_column: int = 20,
    ) -> TaskBoardMetadata:
        """Build one kanban-like backlog board for a Task Flow slice."""

        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_labels = _normalize_labels(labels)

        async def _op(repo: TaskFlowRepository) -> TaskBoardMetadata:
            await _ensure_profile_exists(repo, profile_id)
            if normalized_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )
            rows = await repo.list_tasks(
                profile_id=profile_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                flow_id=normalized_flow_id,
            )
            filtered_rows = [
                row for row in rows if _task_matches_required_labels(row=row, labels=normalized_labels)
            ]
            now_utc = datetime.now(timezone.utc)
            column_counts: dict[str, int] = {column_id: 0 for column_id, _title, _statuses in _TASK_BOARD_COLUMNS}
            preview_rows: dict[str, list[Task]] = {column_id: [] for column_id, _title, _statuses in _TASK_BOARD_COLUMNS}
            ready_count = 0
            blocked_count = 0
            running_count = 0
            needs_review_count = 0
            overdue_count = 0

            for row in filtered_rows:
                column_id = _task_board_column_id(status=row.status)
                column_counts[column_id] = column_counts.get(column_id, 0) + 1
                if len(preview_rows[column_id]) < max(limit_per_column, 1):
                    preview_rows[column_id].append(row)
                normalized_status = str(row.status or "").strip().lower()
                if normalized_status == "todo":
                    ready_count += 1
                if normalized_status == "blocked":
                    blocked_count += 1
                if normalized_status in {"claimed", "running"}:
                    running_count += 1
                if normalized_status == "review":
                    needs_review_count += 1
                if _is_task_overdue(row=row, now_utc=now_utc):
                    overdue_count += 1

            columns: list[TaskBoardColumnMetadata] = []
            for column_id, title, statuses in _TASK_BOARD_COLUMNS:
                tasks = tuple(
                    [await _build_task_metadata(repo, row) for row in preview_rows.get(column_id, [])]
                )
                columns.append(
                    TaskBoardColumnMetadata(
                        id=column_id,
                        title=title,
                        statuses=statuses,
                        count=column_counts.get(column_id, 0),
                        tasks=tasks,
                    )
                )

            return TaskBoardMetadata(
                generated_at=now_utc,
                profile_id=profile_id,
                flow_id=normalized_flow_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                labels=normalized_labels,
                limit_per_column=max(limit_per_column, 1),
                total_count=len(filtered_rows),
                ready_count=ready_count,
                blocked_count=blocked_count,
                running_count=running_count,
                needs_review_count=needs_review_count,
                overdue_count=overdue_count,
                columns=tuple(columns),
            )

        return await self._with_repo(_op)

    async def list_dependencies(
        self,
        *,
        profile_id: str,
        task_id: str,
    ) -> list[TaskDependencyMetadata]:
        """List dependency edges for one task."""

        async def _op(repo: TaskFlowRepository) -> list[TaskDependencyMetadata]:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            dependencies = await repo.list_dependencies(task_id=task.id)
            return [_to_dependency_metadata(edge) for edge in dependencies]

        return await self._with_repo(_op)

    async def add_dependency(
        self,
        *,
        profile_id: str,
        task_id: str,
        depends_on_task_id: str,
        satisfied_on_status: str = "completed",
    ) -> TaskDependencyMetadata:
        """Add one dependency edge and reconcile task readiness."""

        normalized_status = _normalize_status(satisfied_on_status)

        async def _op(repo: TaskFlowRepository) -> TaskDependencyMetadata:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _create_dependency_edge(
                repo=repo,
                profile_id=profile_id,
                task_id=task.id,
                depends_on_task_id=depends_on_task_id,
                satisfied_on_status=normalized_status,
            )
            refreshed_task = await repo.get_task(profile_id=profile_id, task_id=task.id)
            if refreshed_task is not None:
                await _reconcile_task_readiness_after_dependency_change(
                    repo=repo,
                    task=refreshed_task,
                )
            edge = await repo.get_dependency(
                task_id=task.id,
                depends_on_task_id=depends_on_task_id,
            )
            if edge is None:
                raise TaskFlowServiceError(
                    error_code="task_dependency_not_found",
                    reason="Dependency edge not found after creation",
                )
            return _to_dependency_metadata(edge)

        return await self._with_repo(_op)

    async def remove_dependency(
        self,
        *,
        profile_id: str,
        task_id: str,
        depends_on_task_id: str,
    ) -> bool:
        """Remove one dependency edge and reconcile task readiness."""

        async def _op(repo: TaskFlowRepository) -> bool:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            deleted = await repo.delete_dependency(
                task_id=task.id,
                depends_on_task_id=depends_on_task_id,
            )
            if not deleted:
                raise TaskFlowServiceError(
                    error_code="task_dependency_not_found",
                    reason="Dependency edge not found",
                )
            refreshed_task = await repo.get_task(profile_id=profile_id, task_id=task.id)
            if refreshed_task is not None:
                await _reconcile_task_readiness_after_dependency_change(
                    repo=repo,
                    task=refreshed_task,
                )
            return True

        return await self._with_repo(_op)

    async def list_task_runs(
        self,
        *,
        profile_id: str,
        task_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskRunMetadata]:
        """List execution attempts for one task or profile."""

        async def _op(repo: TaskFlowRepository) -> list[TaskRunMetadata]:
            normalized_task_id = _normalize_optional_text(task_id)
            if normalized_task_id is not None:
                task = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
                runs = await repo.list_task_runs(
                    profile_id=profile_id,
                    task_id=task.id,
                    limit=limit,
                )
            else:
                await _ensure_profile_exists(repo, profile_id)
                runs = await repo.list_task_runs(profile_id=profile_id, limit=limit)
            return [_to_task_run_metadata(run) for run in runs]

        return await self._with_repo(_op)

    async def get_task_run(
        self,
        *,
        profile_id: str,
        task_run_id: int,
        task_id: str | None = None,
    ) -> TaskRunMetadata:
        """Get one execution attempt."""

        async def _op(repo: TaskFlowRepository) -> TaskRunMetadata:
            normalized_task_id = _normalize_optional_text(task_id)
            if normalized_task_id is not None:
                task = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
                run = await repo.get_task_run(task_run_id=task_run_id, task_id=task.id)
            else:
                await _ensure_profile_exists(repo, profile_id)
                run = await repo.get_task_run(task_run_id=task_run_id)
                if run is not None:
                    task = await repo.get_task(profile_id=profile_id, task_id=run.task_id)
                    if task is None:
                        run = None
            if run is None:
                raise TaskFlowServiceError(error_code="task_run_not_found", reason="Task run not found")
            return _to_task_run_metadata(run)

        return await self._with_repo(_op)

    async def list_tasks(
        self,
        *,
        profile_id: str,
        statuses: Sequence[str] = (),
        owner_type: str | None = None,
        owner_ref: str | None = None,
        flow_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskMetadata]:
        """List tasks with optional filters."""

        normalized_statuses = _normalize_statuses(statuses)
        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )

        async def _op(repo: TaskFlowRepository) -> list[TaskMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            rows = await repo.list_tasks(
                profile_id=profile_id,
                statuses=normalized_statuses or None,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                flow_id=_normalize_optional_text(flow_id),
                limit=limit,
            )
            return [await _build_task_metadata(repo, row) for row in rows]

        return await self._with_repo(_op)

    async def update_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        title: str | None = None,
        prompt: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        due_at: datetime | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reviewer_type: str | None = None,
        reviewer_ref: str | None = None,
        requires_review: bool | None = None,
        labels: Sequence[str] | None = None,
        blocked_reason_code: str | None = None,
        blocked_reason_text: str | None = None,
    ) -> TaskMetadata:
        """Update mutable task fields."""

        normalized_status = _normalize_status(status) if status is not None else None
        normalized_title = (
            _normalize_required_text(title, field_name="title") if title is not None else None
        )
        normalized_prompt = (
            _normalize_required_text(prompt, field_name="prompt") if prompt is not None else None
        )
        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )
        _validate_owner_pair(
            owner_type=reviewer_type,
            owner_ref=reviewer_ref,
            allow_missing=True,
        )

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            await _ensure_profile_exists(repo, profile_id)
            current_row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if current_row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            owner_changed = (
                (normalized_owner_type is not None and normalized_owner_type != current_row.owner_type)
                or (normalized_owner_ref is not None and normalized_owner_ref != current_row.owner_ref)
            )
            effective_owner_type = normalized_owner_type or current_row.owner_type
            effective_owner_ref = normalized_owner_ref or current_row.owner_ref
            effective_reviewer_type = _normalize_optional_text(reviewer_type)
            effective_reviewer_ref = _normalize_optional_text(reviewer_ref)
            if reviewer_type is None and reviewer_ref is None:
                effective_reviewer_type = current_row.reviewer_type
                effective_reviewer_ref = current_row.reviewer_ref
            effective_status = normalized_status
            if owner_changed:
                if effective_status in {"claimed", "running"}:
                    raise TaskFlowServiceError(
                        error_code="task_owner_reassign_conflict",
                        reason="Running or claimed task cannot be reassigned without leaving active execution",
                    )
                if effective_status is None and current_row.status in {"claimed", "running"}:
                    effective_status = "todo"
            await _ensure_actor_refs_exist(
                repo,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
                reviewer_type=effective_reviewer_type,
                reviewer_ref=effective_reviewer_ref,
            )
            row = await repo.update_task(
                profile_id=profile_id,
                task_id=task_id,
                title=normalized_title,
                prompt=normalized_prompt,
                status=effective_status,
                priority=priority,
                due_at=due_at,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                reviewer_type=_normalize_optional_text(reviewer_type),
                reviewer_ref=_normalize_optional_text(reviewer_ref),
                requires_review=requires_review,
                labels_json=(json.dumps(_normalize_labels(labels)) if labels is not None else None),
                blocked_reason_code=blocked_reason_code,
                blocked_reason_text=blocked_reason_text,
            )
            if row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            if effective_status == "completed":
                await _reconcile_dependent_tasks(
                    repo=repo,
                    profile_id=profile_id,
                    task_id=row.id,
                )
            return await _build_task_metadata(repo, row)

        return await self._with_repo(_op)

    async def reconcile_completed_task(self, *, profile_id: str, task_id: str) -> None:
        """Re-evaluate dependent tasks after one task reaches completed state."""

        async def _op(repo: TaskFlowRepository) -> None:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            await _reconcile_dependent_tasks(
                repo=repo,
                profile_id=profile_id,
                task_id=task_id,
            )

        await self._with_repo(_op)

    async def summarize_human_tasks(
        self,
        *,
        profile_id: str,
        owner_ref: str,
        limit: int = 5,
    ) -> HumanTaskStartupSummary:
        """Return one startup summary for a human inbox across assignee and review work."""

        normalized_owner_ref = _normalize_required_text(owner_ref, field_name="owner_ref")

        async def _op(repo: TaskFlowRepository) -> HumanTaskStartupSummary:
            await _ensure_profile_exists(repo, profile_id)
            all_rows = await repo.list_tasks(
                profile_id=profile_id,
                statuses=_VISIBLE_HUMAN_STATUSES,
            )
            filtered_rows = [
                row for row in all_rows if _task_matches_human_inbox(row=row, owner_ref=normalized_owner_ref)
            ]
            preview_rows = filtered_rows[: max(limit, 1)]
            metadata_rows = tuple([await _build_task_metadata(repo, row) for row in preview_rows])
            status_counts = {status_name: 0 for status_name in _VISIBLE_HUMAN_STATUSES}
            for row in filtered_rows:
                status_counts[row.status] = status_counts.get(row.status, 0) + 1
            return HumanTaskStartupSummary(
                owner_ref=normalized_owner_ref,
                total_count=len(filtered_rows),
                todo_count=status_counts.get("todo", 0),
                blocked_count=status_counts.get("blocked", 0),
                review_count=status_counts.get("review", 0),
                tasks=metadata_rows,
            )

        return await self._with_repo(_op)

    async def _with_repo(
        self,
        op: Callable[[TaskFlowRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = TaskFlowRepository(session)
            return await op(repo)

    async def shutdown(self) -> None:
        """Dispose owned async engine when the service created it."""

        if self._engine is None:
            return
        await self._engine.dispose()


def get_task_flow_service(settings: Settings) -> TaskFlowService:
    """Get or create one cached task flow service for the active root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        service = TaskFlowService(session_factory=session_factory, settings=settings, engine=engine)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_task_flow_services() -> None:
    """Reset cached task flow service instances."""

    _SERVICES_BY_ROOT.clear()


async def reset_task_flow_services_async() -> None:
    """Dispose and clear cached task flow service instances."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()


async def _ensure_profile_exists(repo: TaskFlowRepository, profile_id: str) -> None:
    """Require that the selected profile exists before mutating task flow state."""

    if await profile_exists(repo._session, profile_id=profile_id):  # type: ignore[attr-defined]
        return
    raise TaskFlowServiceError(error_code="profile_not_found", reason="Profile not found")


async def _ensure_actor_refs_exist(
    repo: TaskFlowRepository,
    *,
    owner_type: str | None,
    owner_ref: str | None,
    reviewer_type: str | None,
    reviewer_ref: str | None,
) -> None:
    """Validate AI profile references for owners/reviewers before persistence."""

    if owner_type == "ai_profile" and owner_ref is not None:
        await _ensure_profile_exists(repo, owner_ref)
    if reviewer_type == "ai_profile" and reviewer_ref is not None:
        await _ensure_profile_exists(repo, reviewer_ref)


def _normalize_required_text(value: str | None, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    raise TaskFlowServiceError(error_code=f"invalid_{field_name}", reason=f"{field_name} is required")


def _normalize_optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_labels(labels: Sequence[str] | None) -> tuple[str, ...]:
    if not labels:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _normalize_identifier_list(values: Sequence[str] | None) -> tuple[str, ...]:
    return _normalize_labels(values)


def _normalize_status(status: str) -> str:
    normalized = _normalize_required_text(status, field_name="status").lower()
    if normalized not in _VALID_TASK_STATUSES:
        raise TaskFlowServiceError(error_code="invalid_status", reason=f"Unsupported task status: {status}")
    return normalized


def _normalize_statuses(statuses: Sequence[str] | None) -> tuple[str, ...]:
    if not statuses:
        return ()
    return tuple(_normalize_status(status) for status in statuses)


def _validate_owner_pair(
    *,
    owner_type: str | None,
    owner_ref: str | None,
    allow_missing: bool = False,
) -> None:
    normalized_type = _normalize_optional_text(owner_type)
    normalized_ref = _normalize_optional_text(owner_ref)
    if normalized_type is None and normalized_ref is None and allow_missing:
        return
    if normalized_type is None or normalized_ref is None:
        raise TaskFlowServiceError(
            error_code="invalid_owner",
            reason="owner_type and owner_ref must be provided together",
        )
    if normalized_type not in _VALID_OWNER_TYPES:
        raise TaskFlowServiceError(
            error_code="invalid_owner_type",
            reason=f"Unsupported owner type: {normalized_type}",
        )


def _new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _to_flow_metadata(row: TaskFlow) -> TaskFlowMetadata:
    return TaskFlowMetadata(
        id=row.id,
        profile_id=row.profile_id,
        title=row.title,
        description=row.description,
        status=row.status,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        default_owner_type=row.default_owner_type,
        default_owner_ref=row.default_owner_ref,
        labels=tuple(_decode_labels(row.labels_json)),
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_task_metadata(
    row: Task,
    *,
    depends_on_task_ids: tuple[str, ...] = (),
) -> TaskMetadata:
    return TaskMetadata(
        id=row.id,
        profile_id=row.profile_id,
        flow_id=row.flow_id,
        title=row.title,
        prompt=row.prompt,
        status=row.status,
        priority=row.priority,
        due_at=row.due_at,
        ready_at=row.ready_at,
        owner_type=row.owner_type,
        owner_ref=row.owner_ref,
        reviewer_type=row.reviewer_type,
        reviewer_ref=row.reviewer_ref,
        source_type=row.source_type,
        source_ref=row.source_ref,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        labels=tuple(_decode_labels(row.labels_json)),
        depends_on_task_ids=depends_on_task_ids,
        requires_review=bool(row.requires_review),
        blocked_reason_code=row.blocked_reason_code,
        blocked_reason_text=row.blocked_reason_text,
        current_attempt=row.current_attempt,
        last_session_id=row.last_session_id,
        last_run_id=row.last_run_id,
        last_error_code=row.last_error_code,
        last_error_text=row.last_error_text,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _build_task_metadata(repo: TaskFlowRepository, row: Task) -> TaskMetadata:
    dependencies = await repo.list_dependencies(task_id=row.id)
    return _to_task_metadata(
        row,
        depends_on_task_ids=tuple(edge.depends_on_task_id for edge in dependencies),
    )


def _to_dependency_metadata(row: TaskDependency) -> TaskDependencyMetadata:
    return TaskDependencyMetadata(
        task_id=row.task_id,
        depends_on_task_id=row.depends_on_task_id,
        satisfied_on_status=row.satisfied_on_status,
        created_at=row.created_at,
    )


def _to_task_run_metadata(row: TaskRun) -> TaskRunMetadata:
    return TaskRunMetadata(
        id=row.id,
        task_id=row.task_id,
        attempt=row.attempt,
        owner_type=row.owner_type,
        owner_ref=row.owner_ref,
        execution_mode=row.execution_mode,
        status=row.status,
        session_id=row.session_id,
        run_id=row.run_id,
        worker_id=row.worker_id,
        summary=row.summary,
        error_code=row.error_code,
        error_text=row.error_text,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _decode_labels(raw_json: str) -> list[str]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def _task_matches_required_labels(*, row: Task, labels: Sequence[str]) -> bool:
    if not labels:
        return True
    task_labels = set(_decode_labels(row.labels_json))
    return set(labels).issubset(task_labels)


def _task_board_column_id(*, status: str) -> str:
    normalized_status = str(status or "").strip().lower()
    for column_id, _title, statuses in _TASK_BOARD_COLUMNS:
        if normalized_status in statuses:
            return column_id
    return "todo"


def _is_task_overdue(*, row: Task, now_utc: datetime) -> bool:
    if row.due_at is None:
        return False
    if row.status in {"completed", "failed", "cancelled"}:
        return False
    due_at = row.due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at < now_utc


def _task_matches_human_inbox(*, row: Task, owner_ref: str) -> bool:
    if row.status in {"todo", "blocked"}:
        return row.owner_type == "human" and row.owner_ref == owner_ref
    if row.status == "review":
        return (
            (row.owner_type == "human" and row.owner_ref == owner_ref)
            or (row.reviewer_type == "human" and row.reviewer_ref == owner_ref)
        )
    return False


async def _reconcile_dependent_tasks(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    task_id: str,
) -> None:
    """Re-evaluate dependent tasks after one prerequisite reaches completion."""

    dependents = await repo.list_dependents(depends_on_task_id=task_id)
    for edge in dependents:
        dependent_row = await repo.get_task(profile_id=profile_id, task_id=edge.task_id)
        if dependent_row is None:
            continue
        await _reconcile_task_readiness(repo=repo, task=dependent_row)


async def _reconcile_task_readiness(
    *,
    repo: TaskFlowRepository,
    task: Task,
) -> Task:
    """Promote dependency-wait tasks into `todo` once all prerequisites are satisfied."""

    dependencies = await repo.list_dependencies(task_id=task.id)
    if not dependencies:
        return task
    for edge in dependencies:
        dependency_row = await repo.get_task(profile_id=task.profile_id, task_id=edge.depends_on_task_id)
        if dependency_row is None:
            return task
        if dependency_row.status != edge.satisfied_on_status:
            return task
    if task.status != "blocked" or task.blocked_reason_code != "dependency_wait":
        return task
    promoted = await repo.update_task(
        profile_id=task.profile_id,
        task_id=task.id,
        status="todo",
        blocked_reason_code=None,
        blocked_reason_text=None,
    )
    return task if promoted is None else promoted


async def _reconcile_task_readiness_after_dependency_change(
    *,
    repo: TaskFlowRepository,
    task: Task,
) -> Task:
    """Apply dependency state after edges were added or removed."""

    dependencies = await repo.list_dependencies(task_id=task.id)
    if not dependencies:
        if task.status == "blocked" and task.blocked_reason_code == "dependency_wait":
            promoted = await repo.update_task(
                profile_id=task.profile_id,
                task_id=task.id,
                status="todo",
                blocked_reason_code=None,
                blocked_reason_text=None,
            )
            return task if promoted is None else promoted
        return task
    for edge in dependencies:
        dependency_row = await repo.get_task(profile_id=task.profile_id, task_id=edge.depends_on_task_id)
        if dependency_row is None or dependency_row.status != edge.satisfied_on_status:
            if task.status != "blocked" or task.blocked_reason_code != "dependency_wait":
                blocked = await repo.update_task(
                    profile_id=task.profile_id,
                    task_id=task.id,
                    status="blocked",
                    blocked_reason_code="dependency_wait",
                    blocked_reason_text="Waiting for dependent tasks to complete.",
                )
                return task if blocked is None else blocked
            return task
    return await _reconcile_task_readiness(repo=repo, task=task)


async def _require_task(
    repo: TaskFlowRepository,
    *,
    profile_id: str,
    task_id: str,
) -> Task:
    """Load one task or raise a structured service error."""

    await _ensure_profile_exists(repo, profile_id)
    row = await repo.get_task(profile_id=profile_id, task_id=task_id)
    if row is None:
        raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
    return row


async def _create_dependency_edge(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    task_id: str,
    depends_on_task_id: str,
    satisfied_on_status: str,
) -> TaskDependency:
    """Create one dependency edge after validation."""

    normalized_depends_on_task_id = _normalize_required_text(
        depends_on_task_id,
        field_name="depends_on_task_id",
    )
    if normalized_depends_on_task_id == task_id:
        raise TaskFlowServiceError(
            error_code="task_dependency_invalid",
            reason="Task cannot depend on itself",
        )
    dependency_row = await repo.get_task(
        profile_id=profile_id,
        task_id=normalized_depends_on_task_id,
    )
    if dependency_row is None:
        raise TaskFlowServiceError(
            error_code="task_dependency_not_found",
            reason=f"Dependency task not found: {normalized_depends_on_task_id}",
        )
    existing = await repo.get_dependency(
        task_id=task_id,
        depends_on_task_id=normalized_depends_on_task_id,
    )
    if existing is not None:
        raise TaskFlowServiceError(
            error_code="task_dependency_exists",
            reason="Dependency edge already exists",
        )
    await _ensure_no_dependency_cycle(
        repo=repo,
        profile_id=profile_id,
        task_id=task_id,
        depends_on_task_id=normalized_depends_on_task_id,
    )
    return await repo.create_dependency(
        task_id=task_id,
        depends_on_task_id=normalized_depends_on_task_id,
        satisfied_on_status=satisfied_on_status,
    )


async def _ensure_no_dependency_cycle(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    task_id: str,
    depends_on_task_id: str,
) -> None:
    """Reject dependency edges that would create a cycle."""

    pending = [depends_on_task_id]
    seen: set[str] = set()
    while pending:
        current_task_id = pending.pop()
        if current_task_id == task_id:
            raise TaskFlowServiceError(
                error_code="task_dependency_cycle",
                reason="Dependency edge would create a cycle",
            )
        if current_task_id in seen:
            continue
        seen.add(current_task_id)
        for edge in await repo.list_dependencies(task_id=current_task_id):
            dependency_row = await repo.get_task(
                profile_id=profile_id,
                task_id=edge.depends_on_task_id,
            )
            if dependency_row is None:
                continue
            pending.append(edge.depends_on_task_id)
