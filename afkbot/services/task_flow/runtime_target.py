"""Shared runtime target assembly for Task Flow AgentLoop turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides


@dataclass(frozen=True, slots=True)
class TaskFlowRuntimeTarget:
    """Effective target and trusted context overrides for one task execution."""

    profile_id: str
    session_id: str
    context_overrides: TurnContextOverrides | None


def build_task_flow_runtime_target(
    *,
    execution_profile_id: str,
    session_id: str,
    task_id: str,
    task_profile_id: str,
    owner_type: str,
    owner_ref: str,
    flow_id: str | None,
    source_type: str,
    source_ref: str | None,
    priority: int,
    attempt: int,
    requires_review: bool,
    labels: tuple[str, ...] = (),
) -> TaskFlowRuntimeTarget:
    """Build one shared task runtime target using the same context layering as chat."""

    from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
    from afkbot.services.ingress.context_overrides import build_ingress_context_overrides
    from afkbot.services.task_flow.context_overrides import build_task_flow_context_overrides

    ingress_overrides = build_ingress_context_overrides(
        transport="taskflow",
        account_id=task_id,
    )
    task_overrides = build_task_flow_context_overrides(
        task_id=task_id,
        task_profile_id=task_profile_id,
        owner_type=owner_type,
        owner_ref=owner_ref,
        flow_id=flow_id,
        source_type=source_type,
        source_ref=source_ref,
        priority=priority,
        attempt=attempt,
        requires_review=requires_review,
        labels=labels,
    )
    return TaskFlowRuntimeTarget(
        profile_id=execution_profile_id,
        session_id=session_id,
        context_overrides=merge_turn_context_overrides(ingress_overrides, task_overrides),
    )
