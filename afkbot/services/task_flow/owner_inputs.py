"""Shared owner/ref normalization for Task Flow tool and CLI surfaces."""

from __future__ import annotations

from afkbot.services.task_flow_principals import normalize_task_owner_type


class TaskOwnerInputError(ValueError):
    """Structured owner selector validation error with stable surface-facing codes."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def resolve_task_owner_inputs(
    *,
    field_prefix: str,
    owner_type: str | None,
    owner_ref: str | None,
) -> tuple[str | None, str | None]:
    """Resolve raw or structured owner inputs into one normalized type/ref pair.

    Task Flow v2 owners are profile-local employees:
    `owner_type=employee`, `owner_ref=<employee_id>`.
    """

    _ = field_prefix
    normalized_type = normalize_task_owner_type(owner_type)
    normalized_ref = _normalize_optional_text(owner_ref)
    if normalized_type is None and normalized_ref is not None:
        normalized_type = "employee"
    return normalized_type, normalized_ref


def _normalize_optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
