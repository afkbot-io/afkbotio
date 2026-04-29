"""Shared helpers for AI executor identity in Task Flow."""

from __future__ import annotations

from afkbot.services.naming import normalize_runtime_name
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id

AI_PROFILE_OWNER_TYPE = "ai_profile"
AI_SUBAGENT_OWNER_TYPE = "ai_subagent"
AI_SUBAGENT_OWNER_TYPE_ALIAS = "subagent"
AI_EXECUTOR_OWNER_TYPES = frozenset((AI_PROFILE_OWNER_TYPE, AI_SUBAGENT_OWNER_TYPE))


def normalize_task_owner_type(owner_type: str | None) -> str | None:
    """Normalize public owner type aliases to the canonical persisted values."""

    normalized = str(owner_type or "").strip().lower()
    if not normalized:
        return None
    if normalized == AI_SUBAGENT_OWNER_TYPE_ALIAS:
        return AI_SUBAGENT_OWNER_TYPE
    return normalized


def is_ai_executor_owner_type(owner_type: str | None) -> bool:
    """Return whether one owner/actor type executes work through the detached AI runtime."""

    normalized = normalize_task_owner_type(owner_type)
    return normalized in AI_EXECUTOR_OWNER_TYPES


def parse_ai_subagent_owner_ref(owner_ref: str | None) -> tuple[str, str] | None:
    """Parse one canonical `<profile_id>:<subagent_name>` ai_subagent owner ref."""

    normalized = str(owner_ref or "").strip()
    if not normalized:
        return None
    profile_id, separator, subagent_name = normalized.partition(":")
    if not separator or not profile_id or not subagent_name or ":" in subagent_name:
        return None
    try:
        normalized_profile_id = validate_profile_id(profile_id)
        normalized_subagent_name = normalize_runtime_name(subagent_name)
    except (InvalidProfileIdError, ValueError):
        return None
    return normalized_profile_id, normalized_subagent_name


def build_ai_subagent_owner_ref(*, profile_id: str, subagent_name: str) -> str:
    """Build one canonical `<profile_id>:<subagent_name>` ai_subagent owner ref."""

    normalized_profile_id = validate_profile_id(str(profile_id).strip())
    normalized_subagent_name = normalize_runtime_name(str(subagent_name).strip())
    return f"{normalized_profile_id}:{normalized_subagent_name}"


def resolve_ai_executor_profile_id(
    *,
    owner_type: str | None,
    owner_ref: str | None,
    task_profile_id: str,
) -> str:
    """Resolve the profile that should host execution for one AI executor owner."""

    normalized_owner_type = normalize_task_owner_type(owner_type)
    normalized_owner_ref = str(owner_ref or "").strip()
    if normalized_owner_type == AI_PROFILE_OWNER_TYPE and normalized_owner_ref:
        return normalized_owner_ref
    if normalized_owner_type == AI_SUBAGENT_OWNER_TYPE:
        parsed = parse_ai_subagent_owner_ref(normalized_owner_ref)
        if parsed is not None:
            return parsed[0]
    return task_profile_id
