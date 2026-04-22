"""Shared actor resolution for Task Flow tool plugins."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.automations.principals import build_automation_principal_ref
from afkbot.services.task_flow.ai_executors import (
    AI_SUBAGENT_OWNER_TYPE,
    parse_ai_subagent_owner_ref,
)
from afkbot.services.tools.base import ToolContext

_TRUSTED_TASKFLOW_DETACHED_KEY = "taskflow_detached_runtime"


@dataclass(frozen=True, slots=True)
class TaskToolActorIdentity:
    """Effective actor identity used by one task tool invocation."""

    actor_type: str
    actor_ref: str
    actor_session_id: str | None


def resolve_task_tool_actor(ctx: ToolContext) -> TaskToolActorIdentity:
    """Return the canonical actor identity for task tools in the current runtime."""

    automation_graph = None
    if isinstance(ctx.runtime_metadata, dict):
        candidate = ctx.runtime_metadata.get("automation_graph")
        if isinstance(candidate, dict):
            automation_graph = candidate

    if isinstance(automation_graph, dict):
        automation_id = automation_graph.get("automation_id")
        if isinstance(automation_id, int):
            return TaskToolActorIdentity(
                actor_type="automation",
                actor_ref=build_automation_principal_ref(
                    profile_id=ctx.profile_id,
                    automation_id=automation_id,
                ),
                actor_session_id=None,
            )

    detached_actor = _trusted_taskflow_detached_actor(ctx=ctx)
    if detached_actor is not None:
        return detached_actor

    return TaskToolActorIdentity(
        actor_type="ai_profile",
        actor_ref=ctx.profile_id,
        actor_session_id=ctx.session_id,
    )


def _trusted_taskflow_detached_actor(*, ctx: ToolContext) -> TaskToolActorIdentity | None:
    """Resolve ai_subagent actor only from trusted detached-runtime context."""

    trusted_context = ctx.trusted_runtime_context
    if not isinstance(trusted_context, dict):
        return None
    detached_payload = trusted_context.get(_TRUSTED_TASKFLOW_DETACHED_KEY)
    if not isinstance(detached_payload, dict):
        return None

    owner_type = str(detached_payload.get("owner_type") or "").strip().lower()
    owner_ref = str(detached_payload.get("owner_ref") or "").strip()
    if owner_type != AI_SUBAGENT_OWNER_TYPE or not owner_ref:
        return None

    parsed_owner = parse_ai_subagent_owner_ref(owner_ref)
    if parsed_owner is None:
        return None
    owner_profile_id, _subagent_name = parsed_owner
    if owner_profile_id != ctx.profile_id:
        return None

    return TaskToolActorIdentity(
        actor_type=AI_SUBAGENT_OWNER_TYPE,
        actor_ref=owner_ref,
        actor_session_id=ctx.session_id,
    )
