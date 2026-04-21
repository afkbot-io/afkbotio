"""Shared actor resolution for Task Flow tool plugins."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.automations.principals import build_automation_principal_ref
from afkbot.services.tools.base import ToolContext


@dataclass(frozen=True, slots=True)
class TaskToolActorIdentity:
    """Effective actor identity used by one task tool invocation."""

    actor_type: str
    actor_ref: str
    actor_session_id: str | None


def resolve_task_tool_actor(ctx: ToolContext) -> TaskToolActorIdentity:
    """Return the canonical actor identity for task tools in the current runtime."""

    automation_graph = None
    taskflow_payload = None
    if isinstance(ctx.runtime_metadata, dict):
        candidate = ctx.runtime_metadata.get("automation_graph")
        if isinstance(candidate, dict):
            automation_graph = candidate
        taskflow_candidate = ctx.runtime_metadata.get("taskflow")
        if isinstance(taskflow_candidate, dict):
            taskflow_payload = taskflow_candidate
    if isinstance(automation_graph, dict):
        automation_id = automation_graph.get("automation_id")
        if isinstance(automation_id, int) and automation_id > 0:
            return TaskToolActorIdentity(
                actor_type="automation",
                actor_ref=build_automation_principal_ref(
                    profile_id=ctx.profile_id,
                    automation_id=automation_id,
                ),
                actor_session_id=None,
            )
    if isinstance(taskflow_payload, dict):
        owner_type = str(taskflow_payload.get("owner_type") or "").strip()
        owner_ref = str(taskflow_payload.get("owner_ref") or "").strip()
        if owner_type == "ai_subagent" and owner_ref:
            return TaskToolActorIdentity(
                actor_type="ai_subagent",
                actor_ref=owner_ref,
                actor_session_id=ctx.session_id,
            )
    return TaskToolActorIdentity(
        actor_type="ai_profile",
        actor_ref=ctx.profile_id,
        actor_session_id=ctx.session_id,
    )
