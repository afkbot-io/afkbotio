"""Task Flow detached-runtime work mode helpers."""

from __future__ import annotations

from collections.abc import Iterable

EXECUTION_WORK_MODE = "execution"
KNOWLEDGE_MAINTENANCE_WORK_MODE = "knowledge_maintenance"
MANAGER_INTAKE_WORK_MODE = "manager_intake"

_KNOWLEDGE_MAINTENANCE_LABELS = {"knowledge-maintenance"}
MANAGER_INTAKE_LABELS = frozenset(
    {
        "manager-intake",
        "cto-intake",
        "review-intake",
        "delegation-intake",
    }
)


def resolve_task_work_mode(
    *,
    source_type: str,
    labels: Iterable[str],
    executor_is_manager: bool,
) -> str:
    """Return the effective detached-runtime work mode for one task."""

    normalized_source_type = str(source_type or "").strip().lower()
    normalized_labels = {str(label).strip().lower() for label in labels if str(label).strip()}
    if normalized_source_type == KNOWLEDGE_MAINTENANCE_WORK_MODE or normalized_labels.intersection(
        _KNOWLEDGE_MAINTENANCE_LABELS
    ):
        return KNOWLEDGE_MAINTENANCE_WORK_MODE
    if executor_is_manager or normalized_labels.intersection(MANAGER_INTAKE_LABELS):
        return MANAGER_INTAKE_WORK_MODE
    return EXECUTION_WORK_MODE


__all__ = [
    "EXECUTION_WORK_MODE",
    "KNOWLEDGE_MAINTENANCE_WORK_MODE",
    "MANAGER_INTAKE_LABELS",
    "MANAGER_INTAKE_WORK_MODE",
    "resolve_task_work_mode",
]
