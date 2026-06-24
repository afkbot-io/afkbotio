"""Repository for Task Flow persistence operations."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import (
    Delete,
    Select,
    and_,
    case,
    delete,
    false,
    func,
    literal,
    not_,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from afkbot.db.dialect import session_dialect_name
from afkbot.db.upsert import upsert_insert_for_session
from afkbot.models.task import Task
from afkbot.models.task_attachment import TaskAttachment
from afkbot.models.task_budget_incident import TaskBudgetIncident
from afkbot.models.task_budget_policy import TaskBudgetPolicy
from afkbot.models.task_context_digest import TaskContextDigest
from afkbot.models.task_delegation_claim import TaskDelegationClaim
from afkbot.models.task_dependency import TaskDependency
from afkbot.models.task_document import TaskDocument
from afkbot.models.task_document_revision import TaskDocumentRevision
from afkbot.models.task_event import TaskEvent
from afkbot.models.task_flow import TaskFlow
from afkbot.models.task_hold import TaskHold
from afkbot.models.task_note import TaskNote
from afkbot.models.task_notification_cursor import TaskNotificationCursor
from afkbot.models.task_recovery_action import TaskRecoveryAction
from afkbot.models.task_relation import TaskRelation
from afkbot.models.task_run import TaskRun
from afkbot.models.task_wake import TaskWake
from afkbot.services.task_flow_principals import EMPLOYEE_EXECUTOR_OWNER_TYPES

_UNSET = object()
_NO_FLOW_BUCKET = "__taskflow_no_flow__"
_MANAGER_ESCALATION_BLOCKER_CODES = frozenset(
    {"manager_reassignment_required", "orchestrator_handoff_required"}
)
_NON_CLAIMABLE_BLOCKER_CODES = frozenset({"dependency_wait", "review_changes_requested"})


def _employee_inbox_candidate_filter(
    *,
    owner_type: str,
    owner_ref: str,
    now_utc: datetime,
) -> ColumnElement[bool]:
    """Build the shared visibility predicate for one employee inbox."""

    review_owner_type_expr = func.coalesce(func.nullif(Task.reviewer_type, ""), Task.owner_type)
    review_owner_ref_expr = func.coalesce(func.nullif(Task.reviewer_ref, ""), Task.owner_ref)
    active_claim_owner_type_expr = func.coalesce(
        func.nullif(Task.claim_owner_type, ""),
        Task.owner_type,
    )
    active_claim_owner_ref_expr = func.coalesce(
        func.nullif(Task.claim_owner_ref, ""),
        Task.owner_ref,
    )
    assigned_owner_candidate = and_(
        Task.owner_type == owner_type,
        Task.owner_ref == owner_ref,
        or_(
            Task.status == "todo",
            and_(
                Task.status.in_(("claimed", "running")),
                or_(
                    Task.claim_source_status.is_(None),
                    Task.claim_source_status != "review",
                ),
            ),
            and_(
                Task.status == "blocked",
                or_(Task.ready_at.is_(None), Task.ready_at <= now_utc),
                or_(
                    Task.blocked_reason_code.is_(None),
                    Task.blocked_reason_code.not_in(tuple(_MANAGER_ESCALATION_BLOCKER_CODES)),
                ),
            ),
        ),
    )
    review_candidate = and_(
        Task.status == "review",
        review_owner_type_expr == owner_type,
        review_owner_ref_expr == owner_ref,
    )
    active_claim_candidate = and_(
        Task.status.in_(("claimed", "running")),
        active_claim_owner_type_expr == owner_type,
        active_claim_owner_ref_expr == owner_ref,
    )
    return or_(assigned_owner_candidate, review_candidate, active_claim_candidate)


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

    async def list_active_flows(
        self,
        *,
        profile_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskFlow]:
        """Return active flows for bounded runtime maintenance scans."""

        statement: Select[tuple[TaskFlow]] = (
            select(TaskFlow)
            .where(TaskFlow.status == "active")
            .order_by(TaskFlow.updated_at.asc(), TaskFlow.created_at.asc())
        )
        if profile_id is not None:
            statement = statement.where(TaskFlow.profile_id == profile_id)
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def update_flow(
        self,
        *,
        flow: TaskFlow,
        title: str,
        description: str | None,
        default_owner_type: str | None,
        default_owner_ref: str | None,
        labels_json: str,
    ) -> TaskFlow:
        """Update editable task flow metadata."""

        flow.title = title
        flow.description = description
        flow.default_owner_type = default_owner_type
        flow.default_owner_ref = default_owner_ref
        flow.labels_json = labels_json
        await self._session.flush()
        await self._session.refresh(flow)
        return flow

    async def delete_flow(self, *, profile_id: str, flow_id: str) -> bool:
        """Delete one task flow row when present."""

        statement: Delete = delete(TaskFlow).where(
            TaskFlow.profile_id == profile_id,
            TaskFlow.id == flow_id,
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def create_task_document(
        self,
        *,
        document_id: str,
        profile_id: str,
        scope_type: str,
        scope_id: str,
        document_key: str,
        title: str,
        body: str,
        created_by_type: str,
        created_by_ref: str,
    ) -> TaskDocument:
        """Create one editable Task Flow document at revision 1."""

        content_hash = task_document_content_hash(body)
        row = TaskDocument(
            id=document_id,
            profile_id=profile_id,
            scope_type=scope_type,
            scope_id=scope_id,
            document_key=document_key,
            title=title,
            body=body,
            content_hash=content_hash,
            revision=1,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            updated_by_type=created_by_type,
            updated_by_ref=created_by_ref,
        )
        self._session.add(row)
        await self._session.flush()
        revision = await self.create_task_document_revision(
            document_id=row.id,
            revision=row.revision,
            title=row.title,
            body=row.body,
            content_hash=content_hash,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
        )
        row.latest_revision_id = revision.id
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_task_document(
        self,
        *,
        profile_id: str,
        scope_type: str,
        scope_id: str,
        document_key: str,
    ) -> TaskDocument | None:
        """Return one document by scope/key."""

        statement: Select[tuple[TaskDocument]] = select(TaskDocument).where(
            TaskDocument.profile_id == profile_id,
            TaskDocument.scope_type == scope_type,
            TaskDocument.scope_id == scope_id,
            TaskDocument.document_key == document_key,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_task_document_by_id(
        self,
        *,
        profile_id: str,
        document_id: str,
    ) -> TaskDocument | None:
        """Return one document by id."""

        statement: Select[tuple[TaskDocument]] = select(TaskDocument).where(
            TaskDocument.profile_id == profile_id,
            TaskDocument.id == document_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_task_documents(
        self,
        *,
        profile_id: str,
        scope_type: str,
        scope_id: str,
    ) -> list[TaskDocument]:
        """Return documents for one scope ordered by key."""

        statement: Select[tuple[TaskDocument]] = (
            select(TaskDocument)
            .where(
                TaskDocument.profile_id == profile_id,
                TaskDocument.scope_type == scope_type,
                TaskDocument.scope_id == scope_id,
            )
            .order_by(TaskDocument.document_key.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def list_task_documents_for_profile(
        self,
        *,
        profile_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        document_key: str | None = None,
        confirmation_status: str | None = None,
        query: str | None = None,
        allowed_flow_document_keys: Sequence[str] = (),
        allowed_task_document_keys: Sequence[str] = (),
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskDocument]:
        """Return profile-scoped documents for the operator document workspace."""

        conditions: list[ColumnElement[bool]] = [TaskDocument.profile_id == profile_id]
        if scope_type is not None:
            conditions.append(TaskDocument.scope_type == scope_type)
        if scope_id is not None:
            conditions.append(TaskDocument.scope_id == scope_id)
        if document_key is not None:
            conditions.append(TaskDocument.document_key == document_key)
        if confirmation_status is not None:
            conditions.append(TaskDocument.confirmation_status == confirmation_status)
        normalized_flow_keys = tuple(
            str(key).strip() for key in allowed_flow_document_keys if str(key).strip()
        )
        normalized_task_keys = tuple(
            str(key).strip() for key in allowed_task_document_keys if str(key).strip()
        )
        if normalized_flow_keys or normalized_task_keys:
            key_conditions: list[ColumnElement[bool]] = []
            if normalized_flow_keys:
                key_conditions.append(
                    and_(
                        TaskDocument.scope_type == "flow",
                        TaskDocument.document_key.in_(normalized_flow_keys),
                    )
                )
            if normalized_task_keys:
                key_conditions.append(
                    and_(
                        TaskDocument.scope_type == "task",
                        TaskDocument.document_key.in_(normalized_task_keys),
                    )
                )
            conditions.append(or_(*key_conditions))
        if query is not None:
            pattern = f"%{query.lower()}%"
            conditions.append(
                or_(
                    func.lower(TaskDocument.document_key).like(pattern),
                    func.lower(TaskDocument.title).like(pattern),
                    func.lower(TaskDocument.body).like(pattern),
                    func.lower(TaskDocument.scope_id).like(pattern),
                    func.lower(TaskDocument.updated_by_ref).like(pattern),
                )
            )

        scope_rank = case(
            (TaskDocument.scope_type == "flow", 0),
            (TaskDocument.scope_type == "task", 1),
            else_=2,
        )
        flow_key_rank = case(
            *(
                (
                    and_(
                        TaskDocument.scope_type == "flow",
                        TaskDocument.document_key == key,
                    ),
                    index,
                )
                for index, key in enumerate(normalized_flow_keys)
            ),
            else_=999,
        )
        task_key_rank = case(
            *(
                (
                    and_(
                        TaskDocument.scope_type == "task",
                        TaskDocument.document_key == key,
                    ),
                    index,
                )
                for index, key in enumerate(normalized_task_keys)
            ),
            else_=999,
        )
        statement: Select[tuple[TaskDocument]] = (
            select(TaskDocument)
            .where(*conditions)
            .order_by(
                scope_rank.asc(),
                flow_key_rank.asc(),
                task_key_rank.asc(),
                TaskDocument.updated_at.desc(),
                TaskDocument.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def update_task_document(
        self,
        *,
        document: TaskDocument,
        title: str,
        body: str,
        updated_by_type: str,
        updated_by_ref: str,
        expected_revision: int | None = None,
    ) -> TaskDocument | None:
        """Update latest document body and append a new revision when content changed."""

        revision = int(expected_revision if expected_revision is not None else document.revision)
        content_hash = task_document_content_hash(body)
        current_content_hash = document.content_hash or task_document_content_hash(document.body)
        if str(document.title or "") == title and current_content_hash == content_hash:
            if document.content_hash != content_hash:
                document.content_hash = content_hash
                await self._session.flush()
                await self._session.refresh(document)
            return document
        next_revision = revision + 1
        statement = (
            update(TaskDocument)
            .where(
                TaskDocument.id == document.id,
                TaskDocument.revision == revision,
            )
            .values(
                revision=next_revision,
                title=title,
                body=body,
                content_hash=content_hash,
                confirmation_status="draft",
                confirmed_revision=None,
                confirmed_by_type=None,
                confirmed_by_ref=None,
                confirmed_at=None,
                updated_by_type=updated_by_type,
                updated_by_ref=updated_by_ref,
            )
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        if not _result_succeeded(result):
            return None
        revision_row = await self.create_task_document_revision(
            document_id=document.id,
            revision=next_revision,
            title=title,
            body=body,
            content_hash=content_hash,
            created_by_type=updated_by_type,
            created_by_ref=updated_by_ref,
        )
        await self._session.execute(
            update(TaskDocument)
            .where(
                TaskDocument.id == document.id,
                TaskDocument.revision == next_revision,
            )
            .values(latest_revision_id=revision_row.id)
        )
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def confirm_task_document(
        self,
        *,
        document: TaskDocument,
        confirmed_by_type: str,
        confirmed_by_ref: str,
        confirmed_at: datetime,
        expected_revision: int | None = None,
    ) -> TaskDocument | None:
        """Mark the current document revision as confirmed."""

        revision = int(expected_revision if expected_revision is not None else document.revision)
        statement = (
            update(TaskDocument)
            .where(
                TaskDocument.id == document.id,
                TaskDocument.revision == revision,
            )
            .values(
                confirmation_status="confirmed",
                confirmed_revision=revision,
                confirmed_by_type=confirmed_by_type,
                confirmed_by_ref=confirmed_by_ref,
                confirmed_at=confirmed_at,
                updated_by_type=confirmed_by_type,
                updated_by_ref=confirmed_by_ref,
            )
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        if not _result_succeeded(result):
            return None
        await self._session.refresh(document)
        return document

    async def create_task_document_revision(
        self,
        *,
        document_id: str,
        revision: int,
        title: str,
        body: str,
        created_by_type: str,
        created_by_ref: str,
        content_hash: str | None = None,
    ) -> TaskDocumentRevision:
        """Append one immutable document revision."""

        resolved_content_hash = content_hash or task_document_content_hash(body)
        row = TaskDocumentRevision(
            document_id=document_id,
            revision=revision,
            title=title,
            body=body,
            content_hash=resolved_content_hash,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_task_document_revisions(
        self,
        *,
        document_id: str,
        limit: int | None = None,
    ) -> list[TaskDocumentRevision]:
        """Return document revisions ordered newest first."""

        statement: Select[tuple[TaskDocumentRevision]] = (
            select(TaskDocumentRevision)
            .where(TaskDocumentRevision.document_id == document_id)
            .order_by(TaskDocumentRevision.revision.desc(), TaskDocumentRevision.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def list_task_comment_events(
        self,
        *,
        task_id: str,
        limit: int | None = None,
    ) -> list[TaskEvent]:
        """Return task comment events ordered from newest to oldest."""

        statement: Select[tuple[TaskEvent]] = (
            select(TaskEvent)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.event_type == "comment_added",
            )
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def delete_task_document(
        self,
        *,
        document: TaskDocument,
        expected_revision: int | None = None,
    ) -> bool:
        """Delete one document and its immutable revisions."""

        revision = int(expected_revision if expected_revision is not None else document.revision)
        guard = await self._session.execute(
            update(TaskDocument)
            .where(
                TaskDocument.id == document.id,
                TaskDocument.revision == revision,
            )
            .values(latest_revision_id=None)
        )
        await self._session.flush()
        if not _result_succeeded(guard):
            return False
        await self._session.execute(
            delete(TaskDocumentRevision).where(TaskDocumentRevision.document_id == document.id)
        )
        result = await self._session.execute(
            delete(TaskDocument).where(
                TaskDocument.id == document.id,
                TaskDocument.revision == revision,
            )
        )
        await self._session.flush()
        return _result_succeeded(result)

    async def delete_task_documents_for_scope(
        self,
        *,
        profile_id: str,
        scope_type: str,
        scope_id: str,
    ) -> int:
        """Delete all documents and revisions linked to one Task Flow scope."""

        rows = await self.list_task_documents(
            profile_id=profile_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        if not rows:
            return 0
        document_ids = [row.id for row in rows]
        await self._session.execute(
            update(TaskDocument)
            .where(TaskDocument.id.in_(document_ids))
            .values(latest_revision_id=None)
        )
        await self._session.flush()
        await self._session.execute(
            delete(TaskDocumentRevision).where(TaskDocumentRevision.document_id.in_(document_ids))
        )
        result = await self._session.execute(
            delete(TaskDocument).where(TaskDocument.id.in_(document_ids))
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def create_task(
        self,
        *,
        task_id: str,
        profile_id: str,
        flow_id: str | None,
        title: str,
        description: str,
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
        last_session_id: str | None = None,
        last_session_profile_id: str | None = None,
    ) -> Task:
        """Create one task row."""

        row = Task(
            id=task_id,
            profile_id=profile_id,
            flow_id=flow_id,
            title=title,
            description=description,
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
            last_session_id=last_session_id,
            last_session_profile_id=last_session_profile_id,
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

    async def get_task_by_session_binding(
        self,
        *,
        profile_id: str,
        session_id: str,
        actor_type: str,
        actor_ref: str,
    ) -> Task | None:
        """Return one active task that binds a live session to one actor."""

        statement: Select[tuple[Task]] = (
            select(Task)
            .where(
                Task.profile_id == profile_id,
                Task.last_session_id == session_id,
                Task.status.in_(("claimed", "running", "review")),
                or_(
                    and_(Task.owner_type == actor_type, Task.owner_ref == actor_ref),
                    and_(
                        Task.claim_owner_type == actor_type,
                        Task.claim_owner_ref == actor_ref,
                    ),
                    and_(
                        Task.status == "review",
                        Task.reviewer_type == actor_type,
                        Task.reviewer_ref == actor_ref,
                    ),
                ),
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_active_task_by_session(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> Task | None:
        """Return one active task that already owns a live session."""

        statement: Select[tuple[Task]] = (
            select(Task)
            .where(
                Task.profile_id == profile_id,
                Task.last_session_id == session_id,
                Task.status.in_(("claimed", "running", "review")),
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def delete_task(self, *, profile_id: str, task_id: str) -> bool:
        """Delete one task row when present."""

        statement: Delete = delete(Task).where(
            Task.profile_id == profile_id,
            Task.id == task_id,
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def delete_task_dependencies(self, *, task_id: str) -> int:
        """Delete dependency edges where the task is either source or prerequisite."""

        statement: Delete = delete(TaskDependency).where(
            or_(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == task_id,
            )
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_task_events(self, *, task_id: str) -> int:
        """Delete all task events for one task."""

        statement: Delete = delete(TaskEvent).where(TaskEvent.task_id == task_id)
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_task_runs(self, *, task_id: str) -> int:
        """Delete all task runs for one task."""

        statement: Delete = delete(TaskRun).where(TaskRun.task_id == task_id)
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_task_v2_children(self, *, task_id: str) -> int:
        """Delete Task Flow v2 control-plane rows linked to one task."""

        total = 0
        statements: tuple[Delete, ...] = (
            delete(TaskWake).where(TaskWake.task_id == task_id),
            delete(TaskRelation).where(
                or_(
                    TaskRelation.source_task_id == task_id,
                    TaskRelation.target_task_id == task_id,
                )
            ),
            delete(TaskNote).where(TaskNote.task_id == task_id),
            delete(TaskContextDigest).where(TaskContextDigest.task_id == task_id),
            delete(TaskDelegationClaim).where(TaskDelegationClaim.source_task_id == task_id),
            delete(TaskRecoveryAction).where(
                or_(
                    TaskRecoveryAction.source_task_id == task_id,
                    TaskRecoveryAction.recovery_task_id == task_id,
                )
            ),
            delete(TaskBudgetIncident).where(TaskBudgetIncident.task_id == task_id),
            delete(TaskHold).where(
                or_(
                    and_(TaskHold.scope_type == "task", TaskHold.scope_id == task_id),
                    and_(TaskHold.scope_type == "tree", TaskHold.scope_id == task_id),
                )
            ),
        )
        for statement in statements:
            result = await self._session.execute(statement)
            total += int(getattr(result, "rowcount", 0) or 0)
        await self._session.flush()
        return total

    async def delete_flow_v2_children(self, *, profile_id: str, flow_id: str) -> int:
        """Delete Task Flow v2 control-plane rows linked directly to one flow."""

        total = 0
        statements: tuple[Delete, ...] = (
            delete(TaskHold).where(TaskHold.profile_id == profile_id, TaskHold.flow_id == flow_id),
            delete(TaskBudgetPolicy).where(
                TaskBudgetPolicy.profile_id == profile_id,
                TaskBudgetPolicy.scope_type == "flow",
                TaskBudgetPolicy.scope_id == flow_id,
            ),
            delete(TaskBudgetIncident).where(
                TaskBudgetIncident.profile_id == profile_id,
                TaskBudgetIncident.flow_id == flow_id,
                TaskBudgetIncident.task_id.is_(None),
            ),
        )
        for statement in statements:
            result = await self._session.execute(statement)
            total += int(getattr(result, "rowcount", 0) or 0)
        await self._session.flush()
        return total

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

    async def list_employee_inbox_tasks(
        self,
        *,
        profile_id: str,
        owner_type: str,
        owner_ref: str,
        limit: int | None = None,
    ) -> list[Task]:
        """Return tasks visible in one employee inbox.

        This mirrors claim ownership: regular work belongs to task owner, review
        work belongs to reviewer when set, and active claimed/running work
        belongs to the persisted claim owner.
        """

        candidate_filter = _employee_inbox_candidate_filter(
            owner_type=owner_type,
            owner_ref=owner_ref,
            now_utc=datetime.now(timezone.utc),
        )
        statement: Select[tuple[Task]] = (
            select(Task)
            .where(
                Task.profile_id == profile_id,
                candidate_filter,
            )
            .order_by(
                Task.priority.desc(),
                Task.due_at.is_(None),
                Task.due_at.asc(),
                Task.created_at.asc(),
            )
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().unique().all())

    async def count_employee_inbox_tasks_by_status(
        self,
        *,
        profile_id: str,
        owner_type: str,
        owner_ref: str,
    ) -> dict[str, int]:
        """Return full employee inbox task counts grouped by current task status."""

        candidate_filter = _employee_inbox_candidate_filter(
            owner_type=owner_type,
            owner_ref=owner_ref,
            now_utc=datetime.now(timezone.utc),
        )
        statement = (
            select(Task.status, func.count(Task.id))
            .where(Task.profile_id == profile_id, candidate_filter)
            .group_by(Task.status)
        )
        rows = (await self._session.execute(statement)).all()
        return {str(status): int(count) for status, count in rows}

    async def list_tasks_by_source(
        self,
        *,
        profile_id: str,
        source_type: str,
        source_ref: str,
        limit: int | None = None,
    ) -> list[Task]:
        """Return tasks created from one source pointer."""

        statement: Select[tuple[Task]] = (
            select(Task)
            .where(
                Task.profile_id == profile_id,
                Task.source_type == source_type,
                Task.source_ref == source_ref,
            )
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def has_active_ai_task(
        self,
        *,
        profile_id: str,
        owner_type: str,
        owner_ref: str,
        exclude_task_id: str | None = None,
    ) -> bool:
        """Return whether the selected employee owner already has another active task."""

        conditions = [
            Task.profile_id == profile_id,
            Task.owner_type == owner_type,
            Task.owner_ref == owner_ref,
            Task.status.in_(("claimed", "running")),
        ]
        if exclude_task_id is not None:
            conditions.append(Task.id != exclude_task_id)
        statement: Select[tuple[str]] = select(Task.id).where(*conditions).limit(1)
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def employee_has_references(self, *, profile_id: str, employee_id: str) -> bool:
        """Return whether tasks or flows still reference one Task Flow employee."""

        task_statement: Select[tuple[str]] = (
            select(Task.id)
            .where(
                Task.profile_id == profile_id,
                or_(
                    and_(
                        Task.owner_type == "employee",
                        Task.owner_ref == employee_id,
                    ),
                    and_(
                        Task.reviewer_type == "employee",
                        Task.reviewer_ref == employee_id,
                    ),
                    and_(
                        Task.claim_owner_type == "employee",
                        Task.claim_owner_ref == employee_id,
                    ),
                ),
            )
            .limit(1)
        )
        if (await self._session.execute(task_statement)).scalar_one_or_none() is not None:
            return True

        flow_statement: Select[tuple[str]] = (
            select(TaskFlow.id)
            .where(
                TaskFlow.profile_id == profile_id,
                TaskFlow.default_owner_type == "employee",
                TaskFlow.default_owner_ref == employee_id,
            )
            .limit(1)
        )
        return (await self._session.execute(flow_statement)).scalar_one_or_none() is not None

    async def update_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        due_at: datetime | None = None,
        ready_at: datetime | None | object = _UNSET,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reviewer_type: str | None | object = _UNSET,
        reviewer_ref: str | None | object = _UNSET,
        requires_review: bool | None = None,
        labels_json: str | None = None,
        last_session_id: str | None | object = _UNSET,
        last_session_profile_id: str | None | object = _UNSET,
        blocked_reason_code: str | None | object = _UNSET,
        blocked_reason_text: str | None | object = _UNSET,
        current_attempt: int | object = _UNSET,
    ) -> Task | None:
        """Update mutable task fields and return the row when found."""

        row = await self.get_task(profile_id=profile_id, task_id=task_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if description is not None:
            row.description = description
        if status is not None:
            row.status = status
        if priority is not None:
            row.priority = priority
        if due_at is not None:
            row.due_at = due_at
        if ready_at is not _UNSET:
            row.ready_at = cast(datetime | None, ready_at)
        if owner_type is not None:
            row.owner_type = owner_type
        if owner_ref is not None:
            row.owner_ref = owner_ref
        if reviewer_type is not _UNSET:
            row.reviewer_type = cast(str | None, reviewer_type)
        if reviewer_ref is not _UNSET:
            row.reviewer_ref = cast(str | None, reviewer_ref)
        if requires_review is not None:
            row.requires_review = requires_review
        if labels_json is not None:
            row.labels_json = labels_json
        if last_session_id is not _UNSET:
            row.last_session_id = cast(str | None, last_session_id)
        if last_session_profile_id is not _UNSET:
            row.last_session_profile_id = cast(str | None, last_session_profile_id)
        if current_attempt is not _UNSET:
            row.current_attempt = max(0, int(cast(int, current_attempt)))
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
            row.claim_owner_type = None
            row.claim_owner_ref = None
            row.claim_source_status = None
            row.claimed_by = None
            row.lease_until = None
        if status == "todo":
            if row.ready_at is None:
                row.ready_at = (
                    datetime.now(row.created_at.tzinfo)
                    if row.created_at.tzinfo is not None
                    else datetime.now(timezone.utc)
                )
        elif status == "blocked":
            if ready_at is _UNSET:
                row.ready_at = None
        elif status is not None and ready_at is _UNSET:
            row.ready_at = None
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

    async def create_task_attachment(
        self,
        *,
        attachment_id: str,
        task_id: str,
        profile_id: str,
        name: str,
        content_type: str | None,
        kind: str,
        byte_size: int,
        sha256: str,
        created_by_type: str,
        created_by_ref: str,
        content: bytes,
    ) -> TaskAttachment:
        """Persist one binary attachment row."""

        row = TaskAttachment(
            id=attachment_id,
            task_id=task_id,
            profile_id=profile_id,
            name=name,
            content_type=content_type,
            kind=kind,
            byte_size=byte_size,
            sha256=sha256,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            content=content,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_task_attachment(
        self,
        *,
        task_id: str,
        attachment_id: str,
    ) -> TaskAttachment | None:
        """Return one task attachment scoped to its task id."""

        statement: Select[tuple[TaskAttachment]] = select(TaskAttachment).where(
            TaskAttachment.task_id == task_id,
            TaskAttachment.id == attachment_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_task_attachments(self, *, task_id: str) -> list[TaskAttachment]:
        """Return attachments ordered by newest first."""

        statement: Select[tuple[TaskAttachment]] = (
            select(TaskAttachment)
            .where(TaskAttachment.task_id == task_id)
            .order_by(TaskAttachment.created_at.desc(), TaskAttachment.id.desc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def count_task_attachments_for_tasks(
        self,
        *,
        task_ids: Sequence[str],
    ) -> dict[str, int]:
        """Return attachment counts keyed by task id for one task slice."""

        normalized_task_ids = tuple(
            dict.fromkeys(str(task_id).strip() for task_id in task_ids if str(task_id).strip())
        )
        if not normalized_task_ids:
            return {}
        rows = await self._session.execute(
            select(TaskAttachment.task_id, func.count(TaskAttachment.id))
            .where(TaskAttachment.task_id.in_(normalized_task_ids))
            .group_by(TaskAttachment.task_id)
        )
        return {str(task_id): int(count or 0) for task_id, count in rows.all()}

    async def delete_task_attachment(
        self,
        *,
        task_id: str,
        attachment_id: str,
    ) -> bool:
        """Delete one task attachment."""

        statement: Delete = delete(TaskAttachment).where(
            TaskAttachment.task_id == task_id,
            TaskAttachment.id == attachment_id,
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return _result_succeeded(result)

    async def delete_task_attachments(self, *, task_id: str) -> int:
        """Delete all attachments linked to one task."""

        statement: Delete = delete(TaskAttachment).where(TaskAttachment.task_id == task_id)
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

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

    async def enqueue_task_wake(
        self,
        *,
        wake_id: str,
        task_id: str,
        profile_id: str,
        flow_id: str | None,
        owner_type: str,
        owner_ref: str,
        reason_code: str,
        idempotency_key: str,
        payload_json: str = "{}",
        source_event_id: int | None = None,
        priority: int = 50,
        run_after: datetime | None = None,
        now_utc: datetime | None = None,
    ) -> TaskWake:
        """Create or coalesce one Task Flow v2 wake request."""

        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            raise ValueError("idempotency_key is required")
        existing = await self.get_task_wake_by_idempotency_key(
            profile_id=profile_id,
            idempotency_key=normalized_key,
        )
        coalesced_at = now_utc or datetime.now(timezone.utc)
        if existing is not None:
            existing.coalesced_count = int(existing.coalesced_count or 0) + 1
            existing.last_coalesced_at = coalesced_at
            if existing.status not in {"pending", "claimed"}:
                existing.status = "pending"
                existing.finished_at = None
                existing.task_run_id = None
                existing.claimed_by = None
                existing.claimed_at = None
            await self._session.flush()
            await self._session.refresh(existing)
            return existing
        existing_open = await self.get_open_task_wake_by_natural_key(
            task_id=task_id,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reason_code=reason_code,
        )
        if existing_open is not None:
            existing_open.coalesced_count = int(existing_open.coalesced_count or 0) + 1
            existing_open.last_coalesced_at = coalesced_at
            existing_open.payload_json = payload_json
            existing_open.priority = max(int(existing_open.priority or 0), int(priority))
            if run_after is not None and (
                existing_open.run_after is None or run_after < existing_open.run_after
            ):
                existing_open.run_after = run_after
            await self._session.flush()
            await self._session.refresh(existing_open)
            return existing_open
        row = TaskWake(
            id=wake_id,
            task_id=task_id,
            profile_id=profile_id,
            flow_id=flow_id,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reason_code=reason_code,
            idempotency_key=normalized_key,
            payload_json=payload_json,
            source_event_id=source_event_id,
            priority=priority,
            run_after=run_after,
            status="pending",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_task_wake_by_idempotency_key(
        self,
        *,
        profile_id: str,
        idempotency_key: str,
    ) -> TaskWake | None:
        """Return one wake by its profile-scoped idempotency key."""

        statement: Select[tuple[TaskWake]] = select(TaskWake).where(
            TaskWake.profile_id == profile_id,
            TaskWake.idempotency_key == idempotency_key,
        ).execution_options(populate_existing=True)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_open_task_wake_by_natural_key(
        self,
        *,
        task_id: str,
        owner_type: str,
        owner_ref: str,
        reason_code: str,
    ) -> TaskWake | None:
        """Return one open wake for the same runnable work item."""

        statement: Select[tuple[TaskWake]] = (
            select(TaskWake)
            .where(
                TaskWake.task_id == task_id,
                TaskWake.owner_type == owner_type,
                TaskWake.owner_ref == owner_ref,
                TaskWake.reason_code == reason_code,
                TaskWake.status.in_(("pending", "claimed")),
            )
            .order_by(TaskWake.created_at.asc(), TaskWake.id.asc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def claim_open_task_wake_for_task(
        self,
        *,
        task_id: str,
        owner_type: str,
        owner_ref: str,
        now_utc: datetime,
        claimed_by: str,
    ) -> TaskWake | None:
        """Mark the highest-priority ready wake for a just-claimed task."""

        statement: Select[tuple[TaskWake]] = (
            select(TaskWake)
            .where(
                TaskWake.task_id == task_id,
                TaskWake.owner_type == owner_type,
                TaskWake.owner_ref == owner_ref,
                TaskWake.status == "pending",
                or_(TaskWake.run_after.is_(None), TaskWake.run_after <= now_utc),
            )
            .order_by(
                TaskWake.priority.desc(),
                TaskWake.run_after.is_(None),
                TaskWake.run_after.asc(),
                TaskWake.created_at.asc(),
                TaskWake.id.asc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        result = await self._session.execute(
            update(TaskWake)
            .where(TaskWake.id == row.id, TaskWake.status == "pending")
            .values(status="claimed", claimed_by=claimed_by, claimed_at=now_utc)
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        if not _result_succeeded(result):
            return None
        await self._session.refresh(row)
        return row

    async def attach_task_run_to_claimed_wakes(
        self,
        *,
        task_id: str,
        claimed_by: str,
        task_run_id: int,
    ) -> int:
        """Attach a task run to wake records claimed by this worker."""

        result = await self._session.execute(
            update(TaskWake)
            .where(
                TaskWake.task_id == task_id,
                TaskWake.claimed_by == claimed_by,
                TaskWake.status == "claimed",
                TaskWake.task_run_id.is_(None),
            )
            .values(task_run_id=task_run_id)
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def release_claimed_task_wakes(
        self,
        *,
        task_id: str,
        claimed_by: str | None = None,
        task_run_id: int | None = None,
        run_after: datetime | None | object = _UNSET,
    ) -> int:
        """Return claimed wake records to pending when the task claim is retried."""

        conditions: list[ColumnElement[bool]] = [
            TaskWake.task_id == task_id,
            TaskWake.status == "claimed",
        ]
        if task_run_id is not None:
            conditions.append(TaskWake.task_run_id == task_run_id)
        if claimed_by is not None:
            conditions.append(TaskWake.claimed_by == claimed_by)
        values: dict[str, object] = {
            "status": "pending",
            "claimed_by": None,
            "claimed_at": None,
            "task_run_id": None,
        }
        if run_after is not _UNSET:
            values["run_after"] = run_after
        result = await self._session.execute(
            update(TaskWake)
            .where(*conditions)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def finish_claimed_task_wakes(
        self,
        *,
        task_id: str,
        claimed_by: str | None = None,
        task_run_id: int | None = None,
        status: str,
        finished_at: datetime,
    ) -> int:
        """Close claimed wake records after runtime resolves the corresponding claim."""

        conditions: list[ColumnElement[bool]] = [
            TaskWake.task_id == task_id,
            TaskWake.status == "claimed",
        ]
        if task_run_id is not None:
            conditions.append(TaskWake.task_run_id == task_run_id)
        if claimed_by is not None:
            conditions.append(TaskWake.claimed_by == claimed_by)
        result = await self._session.execute(
            update(TaskWake)
            .where(*conditions)
            .values(status=status, finished_at=finished_at)
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_task_wakes(
        self,
        *,
        profile_id: str,
        flow_id: str | None = None,
        task_id: str | None = None,
        statuses: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[TaskWake]:
        """Return Task Flow v2 wake records newest first."""

        statement: Select[tuple[TaskWake]] = select(TaskWake).where(
            TaskWake.profile_id == profile_id
        ).execution_options(populate_existing=True)
        if flow_id is not None:
            statement = statement.where(TaskWake.flow_id == flow_id)
        if task_id is not None:
            statement = statement.where(TaskWake.task_id == task_id)
        if statuses:
            statement = statement.where(TaskWake.status.in_(tuple(statuses)))
        statement = statement.order_by(
            func.coalesce(TaskWake.source_event_id, 0).desc(),
            TaskWake.created_at.desc(),
            TaskWake.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def create_task_relation(
        self,
        *,
        relation_id: str,
        profile_id: str,
        flow_id: str | None,
        source_task_id: str,
        target_task_id: str,
        relation_type: str,
        is_blocking: bool = False,
        satisfied_on_status: str | None = None,
        created_by_type: str | None = None,
        created_by_ref: str | None = None,
        details_json: str = "{}",
    ) -> TaskRelation:
        """Create or return one typed relation edge."""

        existing = await self.get_task_relation(
            source_task_id=source_task_id,
            target_task_id=target_task_id,
            relation_type=relation_type,
        )
        if existing is not None:
            return existing
        row = TaskRelation(
            id=relation_id,
            profile_id=profile_id,
            flow_id=flow_id,
            source_task_id=source_task_id,
            target_task_id=target_task_id,
            relation_type=relation_type,
            is_blocking=is_blocking,
            satisfied_on_status=satisfied_on_status,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            details_json=details_json,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_task_relations(
        self,
        *,
        profile_id: str,
        flow_id: str | None = None,
        task_id: str | None = None,
        relation_types: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[TaskRelation]:
        """Return relation edges for one profile, flow, or task."""

        statement: Select[tuple[TaskRelation]] = select(TaskRelation).where(
            TaskRelation.profile_id == profile_id
        )
        if flow_id is not None:
            statement = statement.where(TaskRelation.flow_id == flow_id)
        if task_id is not None:
            statement = statement.where(
                or_(
                    TaskRelation.source_task_id == task_id,
                    TaskRelation.target_task_id == task_id,
                )
            )
        if relation_types:
            statement = statement.where(TaskRelation.relation_type.in_(tuple(relation_types)))
        statement = statement.order_by(TaskRelation.created_at.desc(), TaskRelation.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def get_task_relation(
        self,
        *,
        source_task_id: str,
        target_task_id: str,
        relation_type: str,
    ) -> TaskRelation | None:
        """Return one exact task relation edge."""

        statement: Select[tuple[TaskRelation]] = select(TaskRelation).where(
            TaskRelation.source_task_id == source_task_id,
            TaskRelation.target_task_id == target_task_id,
            TaskRelation.relation_type == relation_type,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

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

    async def list_task_feed_events_for_owner(
        self,
        *,
        profile_id: str,
        owner_type: str,
        owner_ref: str,
        limit: int | None = None,
    ) -> list[TaskEvent]:
        """Return recent feed events whose details reference one employee owner.

        The JSON details payload is intentionally queried with portable LIKE
        predicates so SQLite local runtimes and managed databases share the same
        first-pass behavior.
        """

        statement: Select[tuple[TaskEvent]] = (
            select(TaskEvent)
            .join(Task, Task.id == TaskEvent.task_id)
            .where(
                Task.profile_id == profile_id,
                TaskEvent.event_type.in_(
                    (
                        "mention_created",
                        "recovery_action_created",
                        "runtime_claim_rejected",
                    )
                ),
                TaskEvent.details_json.like(f'%"owner_type": "{owner_type}"%'),
                TaskEvent.details_json.like(f'%"owner_ref": "{owner_ref}"%'),
            )
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def list_task_wakes_for_owner(
        self,
        *,
        profile_id: str,
        owner_type: str,
        owner_ref: str,
        statuses: Sequence[str] | None = ("pending", "claimed"),
        limit: int | None = None,
    ) -> list[TaskWake]:
        """Return recent wake queue rows for one employee owner."""

        statement: Select[tuple[TaskWake]] = (
            select(TaskWake)
            .where(
                TaskWake.profile_id == profile_id,
                TaskWake.owner_type == owner_type,
                TaskWake.owner_ref == owner_ref,
            )
            .order_by(
                func.coalesce(TaskWake.source_event_id, 0).desc(),
                TaskWake.created_at.desc(),
                TaskWake.id.desc(),
            )
        )
        if statuses:
            statement = statement.where(TaskWake.status.in_(tuple(statuses)))
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

        statement = upsert_insert_for_session(self._session, TaskNotificationCursor).values(
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

    async def list_dependencies_for_tasks(
        self,
        *,
        task_ids: Sequence[str],
    ) -> list[TaskDependency]:
        """Return dependencies for many tasks in one query."""

        normalized_task_ids = tuple(
            dict.fromkeys(str(task_id).strip() for task_id in task_ids if str(task_id).strip())
        )
        if not normalized_task_ids:
            return []
        statement: Select[tuple[TaskDependency]] = (
            select(TaskDependency)
            .where(TaskDependency.task_id.in_(normalized_task_ids))
            .order_by(TaskDependency.task_id.asc(), TaskDependency.depends_on_task_id.asc())
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
            statement = statement.join(Task, Task.id == TaskRun.task_id).where(
                Task.profile_id == profile_id
            )
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
        profile_id: str | None = None,
        owner_ref: str | None = None,
    ) -> Task | None:
        """Atomically claim one runnable employee-owned task."""

        active_task = aliased(Task)
        review_owner_type_expr = func.coalesce(func.nullif(Task.reviewer_type, ""), Task.owner_type)
        review_owner_ref_expr = func.coalesce(func.nullif(Task.reviewer_ref, ""), Task.owner_ref)
        claim_owner_type_expr = case(
            (
                Task.status == "review",
                review_owner_type_expr,
            ),
            else_=Task.owner_type,
        )
        claim_owner_ref_expr = case(
            (
                Task.status == "review",
                review_owner_ref_expr,
            ),
            else_=Task.owner_ref,
        )
        active_claim_owner_type_expr = func.coalesce(
            func.nullif(active_task.claim_owner_type, ""),
            active_task.owner_type,
        )
        active_claim_owner_ref_expr = func.coalesce(
            func.nullif(active_task.claim_owner_ref, ""),
            active_task.owner_ref,
        )
        active_owner_exists = (
            select(active_task.id)
            .where(
                active_task.profile_id == Task.profile_id,
                active_claim_owner_type_expr == claim_owner_type_expr,
                active_claim_owner_ref_expr == claim_owner_ref_expr,
                active_task.status.in_(("claimed", "running")),
            )
            .exists()
        )
        todo_candidate = and_(
            Task.status == "todo",
            or_(Task.ready_at.is_(None), Task.ready_at <= now_utc),
        )
        blocked_candidate = and_(
            Task.status == "blocked",
            and_(Task.ready_at.is_not(None), Task.ready_at <= now_utc),
            or_(
                Task.blocked_reason_code.is_(None),
                Task.blocked_reason_code.not_in(tuple(_NON_CLAIMABLE_BLOCKER_CODES)),
            ),
        )
        owner_work_candidate = and_(
            Task.owner_type.in_(tuple(EMPLOYEE_EXECUTOR_OWNER_TYPES)),
            Task.owner_ref != "",
            or_(todo_candidate, blocked_candidate),
        )
        review_candidate = and_(
            Task.status == "review",
            claim_owner_type_expr.in_(tuple(EMPLOYEE_EXECUTOR_OWNER_TYPES)),
            claim_owner_ref_expr != "",
        )
        runnable_candidates = (
            select(Task.id)
            .add_columns(
                Task.profile_id.label("profile_id"),
                Task.flow_id.label("flow_id"),
                func.coalesce(Task.flow_id, literal(_NO_FLOW_BUCKET)).label("flow_bucket"),
                claim_owner_type_expr.label("claim_owner_type"),
                claim_owner_ref_expr.label("claim_owner_ref"),
                Task.status.label("claim_source_status"),
                Task.priority.label("priority"),
                Task.due_at.label("due_at"),
                Task.ready_at.label("ready_at"),
                Task.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=(Task.profile_id, claim_owner_type_expr, claim_owner_ref_expr),
                    order_by=_task_claim_base_ordering(Task),
                )
                .label("owner_rank"),
            )
            .where(
                Task.profile_id == profile_id if profile_id is not None else true(),
                claim_owner_ref_expr == owner_ref if owner_ref is not None else true(),
                or_(owner_work_candidate, review_candidate),
                not_(active_owner_exists),
            )
            .cte("task_claim_candidates")
        )
        task_claim_owner_type_expr = func.coalesce(
            func.nullif(Task.claim_owner_type, ""), Task.owner_type
        )
        active_flow_load = (
            select(
                Task.profile_id.label("profile_id"),
                func.coalesce(Task.flow_id, literal(_NO_FLOW_BUCKET)).label("flow_bucket"),
                func.count(Task.id).label("active_flow_task_count"),
            )
            .where(
                Task.profile_id == profile_id if profile_id is not None else true(),
                task_claim_owner_type_expr.in_(tuple(EMPLOYEE_EXECUTOR_OWNER_TYPES)),
                Task.status.in_(("claimed", "running")),
            )
            .group_by(Task.profile_id, func.coalesce(Task.flow_id, literal(_NO_FLOW_BUCKET)))
            .cte("task_claim_active_flow_load")
        )
        # Scheduler semantics:
        # 1. shortlist at most one runnable task per idle `(profile_id, owner_ref)` employee,
        # 2. prefer the highest-priority/due candidate,
        # 3. spread equal-priority work toward flows (including the synthetic no-flow bucket)
        #    that currently have fewer active claimed/running tasks.
        eligible_statement = (
            select(
                runnable_candidates.c.id,
                runnable_candidates.c.claim_owner_type,
                runnable_candidates.c.claim_owner_ref,
                runnable_candidates.c.claim_source_status,
            )
            .select_from(
                runnable_candidates.join(Task, Task.id == runnable_candidates.c.id).outerjoin(
                    active_flow_load,
                    and_(
                        active_flow_load.c.profile_id == runnable_candidates.c.profile_id,
                        active_flow_load.c.flow_bucket == runnable_candidates.c.flow_bucket,
                    ),
                )
            )
            .where(runnable_candidates.c.owner_rank == 1)
            .order_by(
                runnable_candidates.c.priority.desc(),
                runnable_candidates.c.due_at.is_(None),
                runnable_candidates.c.due_at.asc(),
                func.coalesce(active_flow_load.c.active_flow_task_count, 0).asc(),
                runnable_candidates.c.ready_at.is_(None),
                runnable_candidates.c.ready_at.asc(),
                runnable_candidates.c.created_at.asc(),
                runnable_candidates.c.id.asc(),
            )
            .limit(1)
        )
        eligible_statement = _apply_task_claim_locking_for_dialect(
            eligible_statement,
            dialect_name=session_dialect_name(self._session),
        )
        for _attempt in range(3):
            candidate_row = (await self._session.execute(eligible_statement)).first()
            if candidate_row is None:
                return None
            candidate_task_id = str(candidate_row.id)
            candidate_claim_owner_type = str(candidate_row.claim_owner_type)
            candidate_claim_owner_ref = str(candidate_row.claim_owner_ref)
            candidate_claim_source_status = str(candidate_row.claim_source_status)
            update_review_claim_owner_type_expr = func.coalesce(
                func.nullif(Task.reviewer_type, ""), Task.owner_type
            )
            update_review_claim_owner_ref_expr = func.coalesce(
                func.nullif(Task.reviewer_ref, ""), Task.owner_ref
            )
            statement = (
                update(Task)
                .where(
                    Task.id == candidate_task_id,
                    or_(
                        and_(
                            Task.owner_type == candidate_claim_owner_type,
                            Task.owner_ref == candidate_claim_owner_ref,
                            Task.status == "todo",
                            or_(Task.ready_at.is_(None), Task.ready_at <= now_utc),
                        ),
                        and_(
                            Task.owner_type == candidate_claim_owner_type,
                            Task.owner_ref == candidate_claim_owner_ref,
                            Task.status == "blocked",
                            and_(Task.ready_at.is_not(None), Task.ready_at <= now_utc),
                            or_(
                                Task.blocked_reason_code.is_(None),
                                Task.blocked_reason_code.not_in(
                                    tuple(_NON_CLAIMABLE_BLOCKER_CODES)
                                ),
                            ),
                        ),
                        and_(
                            Task.status == "review",
                            update_review_claim_owner_type_expr == candidate_claim_owner_type,
                            update_review_claim_owner_ref_expr == candidate_claim_owner_ref,
                        ),
                    ),
                )
                .values(
                    status="claimed",
                    claim_token=claim_token,
                    claim_owner_type=candidate_claim_owner_type,
                    claim_owner_ref=candidate_claim_owner_ref,
                    claim_source_status=candidate_claim_source_status,
                    claimed_by=claimed_by,
                    lease_until=lease_until,
                    ready_at=None,
                    blocked_reason_code=None,
                    blocked_reason_text=None,
                    last_run_id=None,
                    last_error_code=None,
                    last_error_text=None,
                    started_at=None,
                    finished_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            result = await self._session.execute(statement)
            await self._session.flush()
            if not _result_succeeded(result):
                continue
            await self.claim_open_task_wake_for_task(
                task_id=candidate_task_id,
                owner_type=candidate_claim_owner_type,
                owner_ref=candidate_claim_owner_ref,
                now_utc=now_utc,
                claimed_by=claimed_by,
            )
            statement_select: Select[tuple[Task]] = select(Task).where(
                Task.claim_token == claim_token
            )
            return (await self._session.execute(statement_select)).scalar_one_or_none()
        return None

    async def list_expired_claimed_tasks(
        self,
        *,
        now_utc: datetime,
        profile_id: str | None = None,
        owner_ref: str | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """Return employee-owned claimed/running tasks whose lease has expired."""

        conditions = [
            func.coalesce(func.nullif(Task.claim_owner_type, ""), Task.owner_type).in_(
                tuple(EMPLOYEE_EXECUTOR_OWNER_TYPES)
            ),
            Task.status.in_(("claimed", "running")),
            Task.claim_token.is_not(None),
            Task.lease_until.is_not(None),
            Task.lease_until <= now_utc,
        ]
        if profile_id is not None:
            conditions.append(Task.profile_id == profile_id)
        if owner_ref is not None:
            conditions.append(
                func.coalesce(func.nullif(Task.claim_owner_ref, ""), Task.owner_ref) == owner_ref
            )
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
        session_profile_id: str | None = None,
    ) -> bool:
        """Persist the latest run pointer for one claimed task."""

        values: dict[str, object] = {"last_run_id": task_run_id}
        if session_id is not None:
            values["last_session_id"] = session_id
        if session_profile_id is not None:
            values["last_session_profile_id"] = session_profile_id
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
        ready_at: datetime | None,
        status: str = "todo",
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
                status=status,
                claim_token=None,
                claim_owner_type=None,
                claim_owner_ref=None,
                claim_source_status=None,
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
        ready_at: datetime | None | object = _UNSET,
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
            "claim_owner_type": None,
            "claim_owner_ref": None,
            "claim_source_status": None,
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
        if ready_at is not _UNSET:
            values["ready_at"] = ready_at
        elif status == "blocked":
            values["ready_at"] = None
        else:
            values["ready_at"] = None
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
        ready_at: datetime | None,
        status: str = "todo",
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
                status=status,
                claim_token=None,
                claim_owner_type=None,
                claim_owner_ref=None,
                claim_source_status=None,
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

    async def block_task_claim(
        self,
        *,
        task_id: str,
        claim_token: str,
        reason_code: str,
        reason_text: str,
    ) -> bool:
        """Move the currently claimed task to blocked only when the lease token still matches."""

        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.claim_token == claim_token,
                Task.status.in_(("claimed", "running")),
            )
            .values(
                status="blocked",
                claim_token=None,
                claim_owner_type=None,
                claim_owner_ref=None,
                claim_source_status=None,
                claimed_by=None,
                lease_until=None,
                ready_at=None,
                started_at=None,
                blocked_reason_code=reason_code,
                blocked_reason_text=reason_text,
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
            "claim_owner_type": None,
            "claim_owner_ref": None,
            "claim_source_status": None,
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


def task_document_content_hash(body: str) -> str:
    """Return a stable hash for one Task Flow document body."""

    return hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()


def _apply_task_claim_locking_for_dialect(
    statement: Select[Any],
    *,
    dialect_name: str,
) -> Select[Any]:
    if dialect_name == "postgresql":
        return statement.with_for_update(skip_locked=True, of=Task)
    return statement


def _task_claim_base_ordering(task_ref: Any) -> tuple[ColumnElement[object], ...]:
    """Return the canonical scheduling order used for one runnable task candidate."""

    return (
        cast(ColumnElement[object], task_ref.priority.desc()),
        cast(ColumnElement[object], task_ref.due_at.is_(None)),
        cast(ColumnElement[object], task_ref.due_at.asc()),
        cast(ColumnElement[object], task_ref.ready_at.is_(None)),
        cast(ColumnElement[object], task_ref.ready_at.asc()),
        cast(ColumnElement[object], task_ref.created_at.asc()),
        cast(ColumnElement[object], task_ref.id.asc()),
    )


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
        normalized_statuses = tuple(
            str(item).strip() for item in updated_visible_statuses if str(item).strip()
        )
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
