"""Helpers for Task Flow tool scope resolution in normal and background runtimes."""

from __future__ import annotations

from collections.abc import Set

from afkbot.services.tools.base import ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters


def resolve_task_target_profile(
    *,
    ctx: ToolContext,
    payload: ToolParameters,
    explicit_fields: Set[str] | set[str],
) -> str:
    """Resolve effective Task Flow profile, including taskflow runtime backlog scope."""

    runtime_task_profile_id = _runtime_task_profile_id(ctx=ctx)
    explicit_profile = bool({"profile_id", "profile_key"}.intersection(explicit_fields))
    requested_profile_id = payload.effective_profile_id
    if runtime_task_profile_id is None:
        return requested_profile_id
    if explicit_profile:
        return requested_profile_id
    if requested_profile_id == "default" and ctx.profile_id != "default":
        return runtime_task_profile_id
    return requested_profile_id


def ensure_task_target_scope(
    *,
    ctx: ToolContext,
    target_profile_id: str,
) -> ToolResult | None:
    """Return scope error when Task Flow tool targets a forbidden profile."""

    runtime_task_profile_id = _runtime_task_profile_id(ctx=ctx)
    allowed_profiles = {ctx.profile_id}
    if runtime_task_profile_id is not None:
        allowed_profiles.add(runtime_task_profile_id)
    if target_profile_id in allowed_profiles:
        return None
    return ToolResult.error(error_code="profile_not_found", reason="Profile not found")


def runtime_task_id(*, ctx: ToolContext) -> str | None:
    """Return current runtime task id when the tool runs inside Task Flow execution."""

    taskflow_payload = _runtime_taskflow_payload(ctx=ctx)
    if taskflow_payload is None:
        return None
    value = str(taskflow_payload.get("task_id") or "").strip()
    return value or None


def _runtime_task_profile_id(*, ctx: ToolContext) -> str | None:
    taskflow_payload = _runtime_taskflow_payload(ctx=ctx)
    if taskflow_payload is None:
        return None
    value = str(taskflow_payload.get("task_profile_id") or "").strip()
    return value or None


def _runtime_taskflow_payload(*, ctx: ToolContext) -> dict[str, object] | None:
    runtime_metadata = ctx.runtime_metadata
    if not isinstance(runtime_metadata, dict):
        return None
    taskflow_payload = runtime_metadata.get("taskflow")
    if not isinstance(taskflow_payload, dict):
        return None
    return taskflow_payload
