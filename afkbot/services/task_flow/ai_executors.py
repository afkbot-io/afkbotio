"""Shared helpers for employee executor identity in Task Flow."""

from __future__ import annotations

from afkbot.services.employees.errors import EmployeeServiceError
from afkbot.services.employees.ids import validate_employee_id

EMPLOYEE_OWNER_TYPE = "employee"
AI_EXECUTOR_OWNER_TYPES = frozenset((EMPLOYEE_OWNER_TYPE,))
LEGACY_AI_EXECUTOR_OWNER_TYPES = frozenset(("ai_profile", "ai_subagent"))


def normalize_task_owner_type(owner_type: str | None) -> str | None:
    """Normalize public owner types to canonical persisted values."""

    normalized = str(owner_type or "").strip().lower()
    if not normalized:
        return None
    return normalized


def is_ai_executor_owner_type(owner_type: str | None) -> bool:
    """Return whether one owner/actor type executes work through the detached runtime."""

    normalized = normalize_task_owner_type(owner_type)
    return normalized in AI_EXECUTOR_OWNER_TYPES


def parse_employee_owner_ref(owner_ref: str | None) -> str | None:
    """Parse one canonical profile-local employee owner ref."""

    normalized = str(owner_ref or "").strip()
    if not normalized:
        return None
    try:
        return validate_employee_id(normalized)
    except EmployeeServiceError:
        return None


def resolve_ai_executor_profile_id(
    *,
    owner_type: str | None,
    owner_ref: str | None,
    task_profile_id: str,
) -> str:
    """Resolve the profile that should host execution for one employee executor owner."""

    normalized_owner_type = normalize_task_owner_type(owner_type)
    if normalized_owner_type == EMPLOYEE_OWNER_TYPE:
        return task_profile_id
    return task_profile_id
