"""Shared append-only event logging helpers for Task Flow."""

from __future__ import annotations

import json

from afkbot.repositories.task_flow_repo import TaskFlowRepository


def encode_task_event_details(details: dict[str, object] | None) -> str:
    """Serialize one event payload into deterministic JSON."""

    payload = details or {}
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)


async def record_task_event(
    *,
    repo: TaskFlowRepository,
    task_id: str,
    event_type: str,
    task_run_id: int | None = None,
    actor_type: str | None = None,
    actor_ref: str | None = None,
    message: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Append one immutable task event row."""

    await repo.create_task_event(
        task_id=task_id,
        task_run_id=task_run_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=message,
        from_status=from_status,
        to_status=to_status,
        details_json=encode_task_event_details(details),
    )
