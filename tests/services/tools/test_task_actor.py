"""Unit tests for Task Flow tool actor resolution."""

from __future__ import annotations

from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor


def test_resolve_task_tool_actor_ignores_untrusted_taskflow_subagent_spoof() -> None:
    """Untrusted runtime_metadata.taskflow must not escalate actor to ai_subagent."""

    identity = resolve_task_tool_actor(
        ToolContext(
            profile_id="default",
            session_id="taskflow:task-1",
            run_id=1,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "owner_type": "ai_subagent",
                    "owner_ref": "default:reviewer",
                },
            },
        )
    )

    assert identity.actor_type == "ai_profile"
    assert identity.actor_ref == "default"
    assert identity.actor_session_id == "taskflow:task-1"


def test_resolve_task_tool_actor_uses_trusted_detached_subagent_context() -> None:
    """Trusted detached runtime context should keep ai_subagent actor identity."""

    identity = resolve_task_tool_actor(
        ToolContext(
            profile_id="default",
            session_id="taskflow:task-2",
            run_id=1,
            runtime_metadata={"transport": "taskflow"},
            trusted_runtime_context={
                "taskflow_detached_runtime": {
                    "owner_type": "ai_subagent",
                    "owner_ref": "default:reviewer",
                }
            },
        )
    )

    assert identity.actor_type == "ai_subagent"
    assert identity.actor_ref == "default:reviewer"
    assert identity.actor_session_id == "taskflow:task-2"


def test_resolve_task_tool_actor_rejects_trusted_subagent_profile_mismatch() -> None:
    """Trusted detached ai_subagent owner_ref must match current profile boundary."""

    identity = resolve_task_tool_actor(
        ToolContext(
            profile_id="default",
            session_id="taskflow:task-3",
            run_id=1,
            runtime_metadata={"transport": "taskflow"},
            trusted_runtime_context={
                "taskflow_detached_runtime": {
                    "owner_type": "ai_subagent",
                    "owner_ref": "other:reviewer",
                }
            },
        )
    )

    assert identity.actor_type == "ai_profile"
    assert identity.actor_ref == "default"
    assert identity.actor_session_id == "taskflow:task-3"
