"""Unit tests for Task Flow tool actor resolution."""

from __future__ import annotations

from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor


def test_resolve_task_tool_actor_ignores_untrusted_taskflow_employee_spoof() -> None:
    """Untrusted runtime_metadata.taskflow must not escalate actor to employee."""

    identity = resolve_task_tool_actor(
        ToolContext(
            profile_id="default",
            session_id="taskflow:task-1",
            run_id=1,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "owner_type": "employee",
                    "owner_ref": "cto",
                },
            },
        )
    )

    assert identity.actor_type == "human"
    assert identity.actor_ref == "web-user"
    assert identity.actor_session_id is None


def test_resolve_task_tool_actor_uses_trusted_detached_employee_context() -> None:
    """Trusted detached runtime context should keep employee actor identity."""

    identity = resolve_task_tool_actor(
        ToolContext(
            profile_id="default",
            session_id="taskflow:task-2",
            run_id=1,
            runtime_metadata={"transport": "taskflow"},
            trusted_runtime_context={
                "taskflow_detached_runtime": {
                    "owner_type": "employee",
                    "owner_ref": "cto",
                }
            },
        )
    )

    assert identity.actor_type == "employee"
    assert identity.actor_ref == "cto"
    assert identity.actor_session_id == "taskflow:task-2"


def test_resolve_task_tool_actor_rejects_invalid_trusted_employee_ref() -> None:
    """Trusted detached employee owner_ref must be a safe profile-local employee id."""

    identity = resolve_task_tool_actor(
        ToolContext(
            profile_id="default",
            session_id="taskflow:task-3",
            run_id=1,
            runtime_metadata={"transport": "taskflow"},
            trusted_runtime_context={
                "taskflow_detached_runtime": {
                    "owner_type": "employee",
                    "owner_ref": "../cto",
                }
            },
        )
    )

    assert identity.actor_type == "human"
    assert identity.actor_ref == "web-user"
    assert identity.actor_session_id is None
