"""Service-layer helpers for Task Flow CLI payload generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.task_flow import (
    TaskFlowServiceError,
    TaskMaintenanceSweepMetadata,
    get_task_flow_service,
)
from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.settings import get_settings


async def create_flow_payload(
    *,
    profile_id: str,
    title: str,
    description: str | None,
    created_by_type: str,
    created_by_ref: str,
    default_owner_type: str | None,
    default_owner_ref: str | None,
    labels: tuple[str, ...] = (),
) -> str:
    """Create one flow container and return deterministic JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.create_flow(
            profile_id=profile_id,
            title=title,
            description=description,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            default_owner_type=default_owner_type,
            default_owner_ref=default_owner_ref,
            labels=labels,
        )
        return json.dumps({"task_flow": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_flows_payload(*, profile_id: str) -> str:
    """List flows for one profile."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_flows(profile_id=profile_id)
        return json.dumps(
            {"task_flows": [item.model_dump(mode="json") for item in items]},
            ensure_ascii=True,
        )
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def get_flow_payload(*, profile_id: str, flow_id: str) -> str:
    """Get one flow container."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.get_flow(profile_id=profile_id, flow_id=flow_id)
        return json.dumps({"task_flow": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def create_task_payload(
    *,
    profile_id: str,
    title: str,
    description: str | None = None,
    status: str | None = None,
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
    labels: tuple[str, ...] = (),
    requires_review: bool = False,
    depends_on_task_ids: tuple[str, ...] = (),
) -> str:
    """Create one task and return deterministic JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.create_task(
            profile_id=profile_id,
            title=title,
            description=description,
            status=status,
            created_by_type=created_by_type,
            created_by_ref=created_by_ref,
            flow_id=flow_id,
            priority=priority,
            due_at=due_at,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reviewer_type=reviewer_type,
            reviewer_ref=reviewer_ref,
            source_type=source_type,
            source_ref=source_ref,
            labels=labels,
            requires_review=requires_review,
            depends_on_task_ids=depends_on_task_ids,
        )
        return json.dumps({"task": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_tasks_payload(
    *,
    profile_id: str,
    statuses: tuple[str, ...] = (),
    owner_type: str | None = None,
    owner_ref: str | None = None,
    flow_id: str | None = None,
    limit: int | None = None,
) -> str:
    """List tasks and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_tasks(
            profile_id=profile_id,
            statuses=statuses,
            owner_type=owner_type,
            owner_ref=owner_ref,
            flow_id=flow_id,
            limit=limit,
        )
        return json.dumps({"tasks": [item.model_dump(mode="json") for item in items]}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def build_board_payload(
    *,
    profile_id: str,
    flow_id: str | None = None,
    owner_type: str | None = None,
    owner_ref: str | None = None,
    labels: tuple[str, ...] = (),
    limit_per_column: int = 20,
) -> str:
    """Build one Task Flow board payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        board = await service.build_board(
            profile_id=profile_id,
            flow_id=flow_id,
            owner_type=owner_type,
            owner_ref=owner_ref,
            labels=labels,
            limit_per_column=limit_per_column,
        )
        return json.dumps({"board": board.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def build_human_inbox_payload(
    *,
    profile_id: str,
    owner_ref: str,
    task_limit: int = 5,
    event_limit: int = 5,
    channel: str | None = None,
    mark_seen: bool = False,
) -> str:
    """Build one notification-ready human inbox payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        inbox = await service.build_human_inbox(
            profile_id=profile_id,
            owner_ref=owner_ref,
            task_limit=task_limit,
            event_limit=event_limit,
            channel=channel,
            mark_seen=mark_seen,
        )
        return json.dumps({"inbox": inbox.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_stale_task_claims_payload(
    *,
    profile_id: str,
    limit: int | None = None,
) -> str:
    """List stale Task Flow claims for one profile."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_stale_task_claims(profile_id=profile_id, limit=limit)
        return json.dumps(
            {"stale_task_claims": [item.model_dump(mode="json") for item in items]},
            ensure_ascii=True,
        )
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def sweep_stale_task_claims_payload(
    *,
    profile_id: str,
    limit: int | None = None,
) -> str:
    """Force one maintenance sweep for stale Task Flow claims in one profile."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    runtime = TaskFlowRuntimeService(settings=settings, session_factory=session_factory)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        effective_limit = (
            max(1, limit)
            if limit is not None
            else max(settings.taskflow_runtime_maintenance_batch_size, 1)
        )
        released_count = await runtime.sweep_expired_claims(
            worker_id="taskflow-cli-maintenance",
            limit=effective_limit,
            profile_id=profile_id,
        )
        service = get_task_flow_service(settings)
        remaining = await service.list_stale_task_claims(profile_id=profile_id, limit=effective_limit)
        metadata = TaskMaintenanceSweepMetadata(
            generated_at=datetime.now(timezone.utc),
            profile_id=profile_id,
            limit=effective_limit,
            repaired_count=released_count,
            remaining_count=len(remaining),
            remaining=remaining,
        )
        return json.dumps(
            {"maintenance": metadata.model_dump(mode="json")},
            ensure_ascii=True,
        )
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def list_review_tasks_payload(
    *,
    profile_id: str,
    actor_type: str,
    actor_ref: str,
    flow_id: str | None = None,
    labels: tuple[str, ...] = (),
    limit: int | None = None,
) -> str:
    """List review inbox tasks for one actor."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_review_tasks(
            profile_id=profile_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
            flow_id=flow_id,
            labels=labels,
            limit=limit,
        )
        return json.dumps({"review_tasks": [item.model_dump(mode="json") for item in items]}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_task_events_payload(
    *,
    profile_id: str,
    task_id: str,
    limit: int | None = None,
) -> str:
    """List append-only task events for one task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_task_events(
            profile_id=profile_id,
            task_id=task_id,
            limit=limit,
        )
        return json.dumps({"task_events": [item.model_dump(mode="json") for item in items]}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_task_comments_payload(
    *,
    profile_id: str,
    task_id: str,
    limit: int | None = None,
) -> str:
    """List append-only task comments for one task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_task_comments(
            profile_id=profile_id,
            task_id=task_id,
            limit=limit,
        )
        return json.dumps({"task_comments": [item.model_dump(mode="json") for item in items]}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def add_task_comment_payload(
    *,
    profile_id: str,
    task_id: str,
    message: str,
    actor_type: str,
    actor_ref: str,
    comment_type: str = "note",
    task_run_id: int | None = None,
) -> str:
    """Append one comment to the selected task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.add_task_comment(
            profile_id=profile_id,
            task_id=task_id,
            message=message,
            actor_type=actor_type,
            actor_ref=actor_ref,
            comment_type=comment_type,
            task_run_id=task_run_id,
        )
        return json.dumps({"task_comment": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def get_task_payload(*, profile_id: str, task_id: str) -> str:
    """Get one task and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.get_task(profile_id=profile_id, task_id=task_id)
        return json.dumps({"task": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def update_task_payload(
    *,
    profile_id: str,
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_at: datetime | None = None,
    owner_type: str | None = None,
    owner_ref: str | None = None,
    reviewer_type: str | None = None,
    reviewer_ref: str | None = None,
    requires_review: bool | None = None,
    labels: tuple[str, ...] | None = None,
    blocked_reason_code: str | None = None,
    blocked_reason_text: str | None = None,
    actor_type: str | None = None,
    actor_ref: str | None = None,
) -> str:
    """Update one task and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.update_task(
            profile_id=profile_id,
            task_id=task_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            due_at=due_at,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reviewer_type=reviewer_type,
            reviewer_ref=reviewer_ref,
            requires_review=requires_review,
            labels=labels,
            blocked_reason_code=blocked_reason_code,
            blocked_reason_text=blocked_reason_text,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        return json.dumps({"task": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_dependencies_payload(*, profile_id: str, task_id: str) -> str:
    """List dependency edges for one task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_dependencies(profile_id=profile_id, task_id=task_id)
        return json.dumps(
            {"dependencies": [item.model_dump(mode="json") for item in items]},
            ensure_ascii=True,
        )
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def approve_review_task_payload(
    *,
    profile_id: str,
    task_id: str,
    actor_type: str | None = None,
    actor_ref: str | None = None,
) -> str:
    """Approve one review task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.approve_review_task(
            profile_id=profile_id,
            task_id=task_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        return json.dumps({"task": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def request_review_changes_payload(
    *,
    profile_id: str,
    task_id: str,
    reason_text: str,
    actor_type: str | None = None,
    actor_ref: str | None = None,
    owner_type: str | None = None,
    owner_ref: str | None = None,
    reason_code: str = "review_changes_requested",
) -> str:
    """Request changes for one review task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.request_review_changes(
            profile_id=profile_id,
            task_id=task_id,
            reason_text=reason_text,
            actor_type=actor_type,
            actor_ref=actor_ref,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reason_code=reason_code,
        )
        return json.dumps({"task": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def add_dependency_payload(
    *,
    profile_id: str,
    task_id: str,
    depends_on_task_id: str,
    satisfied_on_status: str = "completed",
    actor_type: str | None = None,
    actor_ref: str | None = None,
) -> str:
    """Add one dependency edge."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.add_dependency(
            profile_id=profile_id,
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            satisfied_on_status=satisfied_on_status,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        return json.dumps({"dependency": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def remove_dependency_payload(
    *,
    profile_id: str,
    task_id: str,
    depends_on_task_id: str,
    actor_type: str | None = None,
    actor_ref: str | None = None,
) -> str:
    """Remove one dependency edge."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        deleted = await service.remove_dependency(
            profile_id=profile_id,
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        return json.dumps(
            {"deleted": deleted, "task_id": task_id, "depends_on_task_id": depends_on_task_id},
            ensure_ascii=True,
        )
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_task_runs_payload(
    *,
    profile_id: str,
    task_id: str | None = None,
    limit: int | None = None,
) -> str:
    """List task execution attempts for one profile or task."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        items = await service.list_task_runs(
            profile_id=profile_id,
            task_id=task_id,
            limit=limit,
        )
        return json.dumps(
            {"task_runs": [item.model_dump(mode="json") for item in items]},
            ensure_ascii=True,
        )
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def get_task_run_payload(
    *,
    profile_id: str,
    task_run_id: int,
) -> str:
    """Get one task execution attempt."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_task_flow_service(settings)
        item = await service.get_task_run(profile_id=profile_id, task_run_id=task_run_id)
        return json.dumps({"task_run": item.model_dump(mode="json")}, ensure_ascii=True)
    except TaskFlowServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def _ensure_profile_exists(
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
) -> None:
    async with session_scope(session_factory) as session:
        profile = await ProfileRepository(session).get(profile_id)
        if profile is None:
            raise TaskFlowServiceError(
                error_code="profile_not_found",
                reason="Profile not found",
            )


def _error_json(*, error_code: str, reason: str) -> str:
    return json.dumps(
        {"ok": False, "error_code": error_code, "reason": reason},
        ensure_ascii=True,
    )
