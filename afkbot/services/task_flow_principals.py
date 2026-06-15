"""Shared helpers for employee Task Flow principal identity."""

from __future__ import annotations

import re

EMPLOYEE_OWNER_TYPE = "employee"
EMPLOYEE_EXECUTOR_OWNER_TYPES = frozenset((EMPLOYEE_OWNER_TYPE,))
_EMPLOYEE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def normalize_task_owner_type(owner_type: str | None) -> str | None:
    """Normalize public owner types to canonical persisted values."""

    normalized = str(owner_type or "").strip().lower()
    if not normalized:
        return None
    return normalized


def is_employee_executor_owner_type(owner_type: str | None) -> bool:
    """Return whether one owner/actor type executes work through the employee runtime."""

    normalized = normalize_task_owner_type(owner_type)
    return normalized in EMPLOYEE_EXECUTOR_OWNER_TYPES


def parse_employee_owner_ref(owner_ref: str | None) -> str | None:
    """Parse one canonical profile-local employee owner ref."""

    normalized = str(owner_ref or "").strip()
    if not normalized:
        return None
    if not _EMPLOYEE_ID_RE.match(normalized):
        return None
    return normalized


def resolve_employee_execution_profile_id(
    *,
    owner_type: str | None,
    owner_ref: str | None,
    task_profile_id: str,
) -> str:
    """Resolve the profile that hosts execution for one employee owner."""

    _ = (owner_type, owner_ref)
    return task_profile_id
