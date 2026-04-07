"""Repository for Task Flow persistence operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import Delete, Select, and_, delete, false, func, or_, select, true, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from afkbot.models.task import Task
from afkbot.models.task_dependency import TaskDependency
from afkbot.models.task_event import TaskEvent
from afkbot.models.task_flow import TaskFlow
from afkbot.models.task_notification_cursor import TaskNotificationCursor
from afkbot.models.task_run import TaskRun

_UNSET = object()


class TaskFlowRepository:
    """Persistence helpers for flows, tasks, dependencies, and runs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_flow(
        self,
        *,
        flow_id: str,
        profile_id: str,
        title: str,
        description: str | None,
        status: str,
        created_by_type: str,
        created_by_ref: str,
        default_owner_type: str | None,
        default_owner_ref: str | None,
        labels_json: str,
    ) -> TaskFlow:
        """Create one task flow row."""

        row = TaskFlow(
            id=flow_id,
            profile_id=profile_id,
            title=title,
            description=description,
            status=status,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            default_owner_type=default_owner_type,
            default_owner_ref=default_owner_ref,
            labels_json=labels_json,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_flow(self, *, profile_id: str, flow_id: str) -> TaskFlow | None:
        """Return one flow row for the selected profile."""

        statement: Select[tuple[TaskFlow]] = select(TaskFlow).where(
            TaskFlow.profile_id == profile_id,
            TaskFlow.id == flow_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_flows(self, *, profile_id: str) -> list[TaskFlow]:
        """Return flows ordered by recent update."""

        statement: Select[tuple[TaskFlow]] = (
            select(TaskFlow)
            .where(TaskFlow.profile_id == profile_id)
            .order_by(TaskFlow.updated_at.desc(), TaskFlow.created_at.desc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def create_task(
        self,
        *,
        task_id: str,
        profile_id: str,
        flow_id: str | None,
        title: str,
        prompt: str,
        status: str,
        priority: int,
        due_at: datetime | None,
        ready_at: datetime | None,
        owner_type: str,
        owner_ref: str,
        reviewer_type: str | None,
        reviewer_ref: str | None,
        source_type: str,
        source_ref: str | None,
        created_by_type: str,
        created_by_ref: str,
        labels_json: str,
        requires_review: bool,
        blocked_reason_code: str | None,
        blocked_reason_text: str | None,
    ) -> Task:
        """Create one task row."""

        row = Task(
            id=task_id,
            profile_id=profile_id,
            flow_id=flow_id,
            title=title,
            prompt=prompt,
            status=status,
            priority=priority,
            due_at=due_at,
            ready_at=ready_at,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reviewer_type=reviewer_type,
            reviewer_ref=reviewer_ref,
            source_type=source_type,
            source_ref=source_ref,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            labels_json=labels_json,
            requires_review=requires_review,
            blocked_reason_code=blocked_reason_code,
            blocked_reason_text=blocked_reason_text,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_task(self, *, profile_id: str, task_id: str) -> Task | None:
        """Return one task row for the selected profile."""

        statement: Select[tuple[Task]] = select(Task).where(
            Task.profile_id == profile_id,
            Task.id == task_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_tasks(
        self,
        *,
        profile_id: str,
        statuses: Sequence[str] | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        flow_id: str | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """Return tasks filtered by profile and optional selectors."""

        statement: Select[tuple[Task]] = select(Task).where(Task.profile_id == profile_id)
        if statuses:
            statement = statement.where(Task.status.in_(tuple(statuses)))
        if owner_type is not None:
            statement = statement.where(Task.owner_type == owner_type)
        if owner_ref is not None:
            statement = statement.where(Task.owner_ref == owner_ref)
        if flow_id is not None:
            statement = statement.where(Task.flow_id == flow_id)
        statement = statement.order_by(
            Task.priority.desc(),
            Task.due_at.is_(None),
            Task.due_at.asc(),
            Task.created_at.asc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

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
        labels_json: str | None = None,
        blocked_reason_code: str | None | object = _UNSET,
        blocked_reason_text: str | None | object = _UNSET,
    ) -> Task | None:
        """Update mutable task fields and return the row when found."""

        row = await self.get_task(profile_id=profile_id, task_id=task_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if prompt is not None:
            row.prompt = prompt
        if status is not None:
            row.status = status
        if priority is not None:
            row.priority = priority
        if due_at is not None:
            row.due_at = due_at
        if owner_type is not None:
            row.owner_type = owner_type
        if owner_ref is not None:
            row.owner_ref = owner_ref
        if reviewer_type is not None:
            row.reviewer_type = reviewer_type
        if reviewer_ref is not None:
            row.reviewer_ref = reviewer_ref
        if requires_review is not None:
            row.requires_review = requires_review
        if labels_json is not None:
            row.labels_json = labels_json
        should_update_blocked_reason = (
            blocked_reason_code is not _UNSET
            or blocked_reason_text is not _UNSET
            or status is not None
        )
        if should_update_blocked_reason:
            next_blocked_reason_code: str | None = (
                None if blocked_reason_code is _UNSET else cast(str | None, blocked_reason_code)
            )
            next_blocked_reason_text: str | None = (
                None if blocked_reason_text is _UNSET else cast(str | None, blocked_reason_text)
            )
            row.blocked_reason_code = next_blocked_reason_code
            row.blocked_reason_text = next_blocked_reason_text
        if status is not None and status not in {"claimed", "running"}:
            row.claim_token = None
            row.claimed_by = None
            row.lease_until = None
        if status == "todo" and row.ready_at is None:
            row.ready_at = (
                datetime.now(row.created_at.tzinfo)
                if row.created_at.tzinfo is not None
                else datetime.now(timezone.utc)
            )
        if status is not None and status not in {"completed", "failed", "cancelled"}:
            row.finished_at = None
        if status in {"completed", "failed", "cancelled"} and row.finished_at is None:
            row.finished_at = (
                datetime.now(row.created_at.tzinfo)
                if row.created_at.tzinfo is not None
                else datetime.now(timezone.utc)
            )
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def create_dependency(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
        satisfied_on_status: str,
    ) -> TaskDependency:
        """Create one dependency edge."""

        row = TaskDependency(
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            satisfied_on_status=satisfied_on_status,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def create_task_event(
        self,
        *,
        task_id: str,
        event_type: str,
        task_run_id: int | None = None,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        message: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        details_json: str = "{}",
    ) -> TaskEvent:
        """Append one immutable task event row."""

        row = TaskEvent(
            task_id=task_id,
            task_run_id=task_run_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_ref=actor_ref,
            message=message,
            from_status=from_status,
            to_status=to_status,
            details_json=details_json,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_task_events(
        self,
        *,
        task_id: str,
        limit: int | None = None,
    ) -> list[TaskEvent]:
        """Return task events ordered from newest to oldest."""

        statement: Select[tuple[TaskEvent]] = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def has_task_run_event(
        self,
        *,
        task_run_id: int,
        event_type: str,
    ) -> bool:
        """Return whether one task run already emitted the selected event type."""

        statement: Select[tuple[int]] = (
            select(TaskEvent.id)
            .where(
                TaskEvent.task_run_id == task_run_id,
                TaskEvent.event_type == event_type,
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def list_task_events_for_tasks(
        self,
        *,
        task_ids: Sequence[str],
        after_event_id: int | None = None,
        limit: int | None = None,
    ) -> list[TaskEvent]:
        """Return recent task events across one task id slice."""

        normalized_ids = tuple(str(task_id).strip() for task_id in task_ids if str(task_id).strip())
        if not normalized_ids:
            return []
        statement: Select[tuple[TaskEvent]] = (
            select(TaskEvent)
            .where(TaskEvent.task_id.in_(normalized_ids))
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        )
        if after_event_id is not None:
            statement = statement.where(TaskEvent.id > after_event_id)
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def count_filtered_task_events_for_tasks(
        self,
        *,
        task_ids: Sequence[str],
        after_event_id: int | None = None,
        event_types: Sequence[str] | None = None,
        updated_visible_statuses: Sequence[str] = (),
        updated_detail_keys: Sequence[str] = (),
    ) -> int:
        """Count task events for one task slice using the selected visibility filter."""

        normalized_ids = tuple(str(task_id).strip() for task_id in task_ids if str(task_id).strip())
        if not normalized_ids:
            return 0
        statement = select(func.count(TaskEvent.id)).where(TaskEvent.task_id.in_(normalized_ids))
        if after_event_id is not None:
            statement = statement.where(TaskEvent.id > after_event_id)
        statement = statement.where(
            _task_event_visibility_predicate(
                event_types=event_types,
                updated_visible_statuses=updated_visible_statuses,
                updated_detail_keys=updated_detail_keys,
            )
        )
        count_value = (await self._session.execute(statement)).scalar_one()
        return int(count_value)

    async def list_filtered_task_events_for_tasks(
        self,
        *,
        task_ids: Sequence[str],
        after_event_id: int | None = None,
        event_types: Sequence[str] | None = None,
        updated_visible_statuses: Sequence[str] = (),
        updated_detail_keys: Sequence[str] = (),
        limit: int | None = None,
    ) -> list[TaskEvent]:
        """List filtered task events for one task slice ordered from newest to oldest."""

        normalized_ids = tuple(str(task_id).strip() for task_id in task_ids if str(task_id).strip())
        if not normalized_ids:
            return []
        statement: Select[tuple[TaskEvent]] = (
            select(TaskEvent)
            .where(TaskEvent.task_id.in_(normalized_ids))
            .where(
                _task_event_visibility_predicate(
                    event_types=event_types,
                    updated_visible_statuses=updated_visible_statuses,
                    updated_detail_keys=updated_detail_keys,
                )
            )
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        )
        if after_event_id is not None:
            statement = statement.where(TaskEvent.id > after_event_id)
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def get_latest_task_event_id_for_tasks(
        self,
        *,
        task_ids: Sequence[str],
        after_event_id: int | None = None,
    ) -> int | None:
        """Return the newest event id across one task slice after the selected cursor."""

        normalized_ids = tuple(str(task_id).strip() for task_id in task_ids if str(task_id).strip())
        if not normalized_ids:
            return None
        statement = select(func.max(TaskEvent.id)).where(TaskEvent.task_id.in_(normalized_ids))
        if after_event_id is not None:
            statement = statement.where(TaskEvent.id > after_event_id)
        latest_event_id = (await self._session.execute(statement)).scalar_one()
        return None if latest_event_id is None else int(latest_event_id)

    async def get_task_notification_cursor(
        self,
        *,
        profile_id: str,
        actor_type: str,
        actor_ref: str,
        channel: str,
    ) -> TaskNotificationCursor | None:
        """Return one notification cursor for actor/channel scope."""

        statement: Select[tuple[TaskNotificationCursor]] = select(TaskNotificationCursor).where(
            TaskNotificationCursor.profile_id == profile_id,
            TaskNotificationCursor.actor_type == actor_type,
            TaskNotificationCursor.actor_ref == actor_ref,
            TaskNotificationCursor.channel == channel,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def upsert_task_notification_cursor(
        self,
        *,
        profile_id: str,
        actor_type: str,
        actor_ref: str,
        channel: str,
        last_seen_event_id: int | None,
    ) -> TaskNotificationCursor:
        """Create or update one notification cursor row."""

        statement = sqlite_insert(TaskNotificationCursor).values(
            profile_id=profile_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
            channel=channel,
            last_seen_event_id=last_seen_event_id,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                TaskNotificationCursor.profile_id,
                TaskNotificationCursor.actor_type,
                TaskNotificationCursor.actor_ref,
                TaskNotificationCursor.channel,
            ],
            set_={
                "last_seen_event_id": last_seen_event_id,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(statement)
        await self._session.flush()
        row = await self.get_task_notification_cursor(
            profile_id=profile_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
            channel=channel,
        )
        if row is None:
            raise RuntimeError("Failed to persist task notification cursor")
        return row

    async def list_dependencies(self, *, task_id: str) -> list[TaskDependency]:
        """Return dependencies for one task."""

        statement: Select[tuple[TaskDependency]] = (
            select(TaskDependency)
            .where(TaskDependency.task_id == task_id)
            .order_by(TaskDependency.depends_on_task_id.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def get_dependency(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
    ) -> TaskDependency | None:
        """Return one dependency edge when present."""

        statement: Select[tuple[TaskDependency]] = select(TaskDependency).where(
            TaskDependency.task_id == task_id,
            TaskDependency.depends_on_task_id == depends_on_task_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_dependents(self, *, depends_on_task_id: str) -> list[TaskDependency]:
        """Return dependency edges that reference one prerequisite task."""

        statement: Select[tuple[TaskDependency]] = (
            select(TaskDependency)
            .where(TaskDependency.depends_on_task_id == depends_on_task_id)
            .order_by(TaskDependency.task_id.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def delete_dependency(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
    ) -> bool:
        """Delete one dependency edge when present."""

        statement: Delete = delete(TaskDependency).where(
            TaskDependency.task_id == task_id,
            TaskDependency.depends_on_task_id == depends_on_task_id,
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def create_task_run(
        self,
        *,
        task_id: str,
        attempt: int,
        owner_type: str,
        owner_ref: str,
        execution_mode: str,
        status: str,
        session_id: str | None,
        run_id: int | None,
        worker_id: str | None,
        started_at: datetime,
    ) -> TaskRun:
        """Persist one task run attempt."""

        row = TaskRun(
            task_id=task_id,
            attempt=attempt,
            owner_type=owner_type,
            owner_ref=owner_ref,
            execution_mode=execution_mode,
            status=status,
            session_id=session_id,
            run_id=run_id,
            worker_id=worker_id,
            started_at=started_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_task_run(
        self,
        *,
        task_run_id: int,
        task_id: str | None = None,
    ) -> TaskRun | None:
        """Return one task run optionally scoped to one task id."""

        statement: Select[tuple[TaskRun]] = select(TaskRun).where(TaskRun.id == task_run_id)
        if task_id is not None:
            statement = statement.where(TaskRun.task_id == task_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_task_runs(
        self,
        *,
        profile_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskRun]:
        """Return task runs ordered by newest attempt first."""

        statement: Select[tuple[TaskRun]] = select(TaskRun)
        if profile_id is not None:
            statement = statement.join(Task, Task.id == TaskRun.task_id).where(Task.profile_id == profile_id)
        if task_id is not None:
            statement = statement.where(TaskRun.task_id == task_id)
        statement = statement.order_by(TaskRun.attempt.desc(), TaskRun.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def claim_next_runnable_task(
        self,
        *,
        now_utc: datetime,
        lease_until: datetime,
        claim_token: str,
        claimed_by: str,
    ) -> Task | None:
        """Atomically claim one runnable AI-owned task."""

        eligible_subquery = (
            select(Task.id)
            .where(
                Task.owner_type == "ai_profile",
                Task.status == "todo",
                or_(Task.ready_at.is_(None), Task.ready_at <= now_utc),
            )
            .order_by(
                Task.priority.desc(),
                Task.due_at.is_(None),
                Task.due_at.asc(),
                Task.ready_at.is_(None),
                Task.ready_at.asc(),
                Task.created_at.asc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(Task)
            .where(Task.id == eligible_subquery)
            .values(
                status="claimed",
                claim_token=claim_token,
                claimed_by=claimed_by,
                lease_until=lease_until,
                last_run_id=None,
                last_error_code=None,
                last_error_text=None,
                blocked_reason_code=None,
                blocked_reason_text=None,
                started_at=None,
                finished_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        if not _result_succeeded(result):
            return None
        statement_select: Select[tuple[Task]] = select(Task).where(Task.claim_token == claim_token)
        return (await self._session.execute(statement_select)).scalar_one_or_none()

    async def list_expired_claimed_tasks(
        self,
        *,
        now_utc: datetime,
        profile_id: str | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """Return AI-owned claimed/running tasks whose lease has expired."""

        conditions = [
            Task.owner_type == "ai_profile",
            Task.status.in_(("claimed", "running")),
            Task.claim_token.is_not(None),
            Task.lease_until.is_not(None),
            Task.lease_until <= now_utc,
        ]
        if profile_id is not None:
            conditions.append(Task.profile_id == profile_id)
        statement: Select[tuple[Task]] = (
            select(Task)
            .where(*conditions)
            .order_by(Task.lease_until.asc(), Task.updated_at.asc(), Task.created_at.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def get_task_by_claim_token(self, *, claim_token: str) -> Task | None:
        """Return one claimed task row by claim token."""

        statement: Select[tuple[Task]] = select(Task).where(Task.claim_token == claim_token)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def attach_task_run(
        self,
        *,
        task_id: str,
        claim_token: str,
        task_run_id: int,
        session_id: str | None = None,
    ) -> bool:
        """Persist the latest run pointer for one claimed task."""

        values: dict[str, object] = {"last_run_id": task_run_id}
        if session_id is not None:
            values["last_session_id"] = session_id
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def mark_task_started(
        self,
        *,
        task_id: str,
        claim_token: str,
        started_at: datetime,
    ) -> bool:
        """Move one claimed task into running state."""

        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
                Task.status == "claimed",
            )
            .values(
                status="running",
                started_at=started_at,
                current_attempt=Task.current_attempt + 1,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def refresh_task_claim(
        self,
        *,
        task_id: str,
        claim_token: str,
        lease_until: datetime,
    ) -> bool:
        """Refresh one in-flight task lease."""

        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
                Task.status.in_(("claimed", "running")),
            )
            .values(lease_until=lease_until)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def release_expired_task_claim(
        self,
        *,
        task_id: str,
        claim_token: str,
        now_utc: datetime,
        ready_at: datetime,
        error_code: str | None = None,
        error_text: str | None = None,
    ) -> bool:
        """Release one expired in-flight task claim back into todo."""

        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
                Task.status.in_(("claimed", "running")),
                Task.lease_until.is_not(None),
                Task.lease_until <= now_utc,
            )
            .values(
                status="todo",
                claim_token=None,
                claimed_by=None,
                lease_until=None,
                ready_at=ready_at,
                started_at=None,
                blocked_reason_code=None,
                blocked_reason_text=None,
                last_error_code=error_code,
                last_error_text=error_text,
                finished_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def finalize_task_claim(
        self,
        *,
        task_id: str,
        claim_token: str,
        status: str,
        finished_at: datetime,
        last_run_id: int | None = None,
        last_error_code: str | None = None,
        last_error_text: str | None = None,
        blocked_reason_code: str | None = None,
        blocked_reason_text: str | None = None,
    ) -> bool:
        """Complete one claimed task into its next durable state."""

        values: dict[str, object] = {
            "status": status,
            "claim_token": None,
            "claimed_by": None,
            "lease_until": None,
            "finished_at": finished_at,
            "last_error_code": last_error_code,
            "last_error_text": last_error_text,
            "blocked_reason_code": blocked_reason_code,
            "blocked_reason_text": blocked_reason_text,
        }
        if last_run_id is not None:
            values["last_run_id"] = last_run_id
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
                Task.status.in_(("claimed", "running")),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def release_task_claim(
        self,
        *,
        task_id: str,
        claim_token: str,
        ready_at: datetime,
        error_code: str | None = None,
        error_text: str | None = None,
    ) -> bool:
        """Release one claimed task back into todo for retry."""

        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
                Task.status.in_(("claimed", "running")),
            )
            .values(
                status="todo",
                claim_token=None,
                claimed_by=None,
                lease_until=None,
                ready_at=ready_at,
                started_at=None,
                blocked_reason_code=None,
                blocked_reason_text=None,
                last_error_code=error_code,
                last_error_text=error_text,
                finished_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def clear_task_claim(
        self,
        *,
        task_id: str,
        claim_token: str,
        last_run_id: int | None = None,
        last_error_code: str | None | object = _UNSET,
        last_error_text: str | None | object = _UNSET,
    ) -> bool:
        """Clear lease/claim metadata without overwriting the current task state."""

        values: dict[str, object] = {
            "claim_token": None,
            "claimed_by": None,
            "lease_until": None,
        }
        if last_run_id is not None:
            values["last_run_id"] = last_run_id
        if last_error_code is not _UNSET:
            values["last_error_code"] = last_error_code
        if last_error_text is not _UNSET:
            values["last_error_text"] = last_error_text
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def update_task_run(
        self,
        *,
        task_run_id: int,
        status: str,
        run_id: int | None | object = _UNSET,
        summary: str | None | object = _UNSET,
        error_code: str | None | object = _UNSET,
        error_text: str | None | object = _UNSET,
        finished_at: datetime | None | object = _UNSET,
    ) -> TaskRun | None:
        """Update one task run row with runtime result metadata."""

        row = await self._session.get(TaskRun, task_run_id)
        if row is None:
            return None
        row.status = status
        if run_id is not _UNSET:
            next_run_id = cast(int | None, run_id)
            row.run_id = next_run_id
        if summary is not _UNSET:
            next_summary = cast(str | None, summary)
            row.summary = next_summary
        if error_code is not _UNSET:
            next_error_code = cast(str | None, error_code)
            row.error_code = next_error_code
        if error_text is not _UNSET:
            next_error_text = cast(str | None, error_text)
            row.error_text = next_error_text
        if finished_at is not _UNSET:
            next_finished_at = cast(datetime | None, finished_at)
            row.finished_at = next_finished_at
        await self._session.flush()
        await self._session.refresh(row)
        return row


def _result_succeeded(result: object) -> bool:
    rowcount = int(getattr(result, "rowcount", 0) or 0)
    return rowcount > 0


def _task_event_visibility_predicate(
    *,
    event_types: Sequence[str] | None,
    updated_visible_statuses: Sequence[str],
    updated_detail_keys: Sequence[str],
) -> ColumnElement[bool]:
    """Build a SQL predicate that mirrors the selected task-event visibility rules."""

    if not event_types:
        return true()
    normalized_event_types = tuple(str(item).strip() for item in event_types if str(item).strip())
    non_updated_types = tuple(item for item in normalized_event_types if item != "updated")
    clauses: list[ColumnElement[bool]] = []
    if non_updated_types:
        clauses.append(TaskEvent.event_type.in_(non_updated_types))
    if "updated" in normalized_event_types:
        normalized_statuses = tuple(str(item).strip() for item in updated_visible_statuses if str(item).strip())
        updated_visibility_clauses: list[ColumnElement[bool]] = []
        if normalized_statuses:
            updated_visibility_clauses.append(TaskEvent.to_status.in_(normalized_statuses))
        updated_visibility_clauses.extend(
            TaskEvent.details_json.like(f'%"{detail_key}"%')
            for detail_key in updated_detail_keys
            if str(detail_key).strip()
        )
        if updated_visibility_clauses:
            clauses.append(
                and_(
                    TaskEvent.event_type == "updated",
                    or_(*updated_visibility_clauses),
                )
            )
    if not clauses:
        return false()
    return or_(*clauses)
