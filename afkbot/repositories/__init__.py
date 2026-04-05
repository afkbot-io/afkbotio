"""Compatibility exports for repository classes.

This package prefers direct module imports such as
`afkbot.repositories.profile_repo.ProfileRepository`. The package-root exports
remain for compatibility, but they load lazily to avoid eager import-time
coupling across the persistence layer.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__all__ = [
    "AutomationRepository",
    "ChatTurnIdempotencyRepository",
    "ChatSessionRepository",
    "ConnectRepository",
    "CredentialsRepository",
    "MemoryRepository",
    "PendingSecureRequestRepository",
    "ProfilePolicyRepository",
    "ProfileRepository",
    "RunRepository",
    "RunlogRepository",
    "SubagentTaskRepository",
    "TaskFlowRepository",
]

_EXPORTS = {
    "AutomationRepository": "afkbot.repositories.automation_repo",
    "ChatTurnIdempotencyRepository": "afkbot.repositories.chat_turn_idempotency_repo",
    "ChatSessionRepository": "afkbot.repositories.chat_session_repo",
    "ConnectRepository": "afkbot.repositories.connect_repo",
    "CredentialsRepository": "afkbot.repositories.credentials_repo",
    "MemoryRepository": "afkbot.repositories.memory_repo",
    "PendingSecureRequestRepository": "afkbot.repositories.pending_secure_request_repo",
    "ProfilePolicyRepository": "afkbot.repositories.profile_policy_repo",
    "ProfileRepository": "afkbot.repositories.profile_repo",
    "RunRepository": "afkbot.repositories.run_repo",
    "RunlogRepository": "afkbot.repositories.runlog_repo",
    "SubagentTaskRepository": "afkbot.repositories.subagent_task_repo",
    "TaskFlowRepository": "afkbot.repositories.task_flow_repo",
}


def __getattr__(name: str) -> object:
    """Resolve compatibility exports lazily at first access."""

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


if TYPE_CHECKING:
    from afkbot.repositories.automation_repo import AutomationRepository
    from afkbot.repositories.chat_session_repo import ChatSessionRepository
    from afkbot.repositories.chat_turn_idempotency_repo import ChatTurnIdempotencyRepository
    from afkbot.repositories.connect_repo import ConnectRepository
    from afkbot.repositories.credentials_repo import CredentialsRepository
    from afkbot.repositories.memory_repo import MemoryRepository
    from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
    from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
    from afkbot.repositories.profile_repo import ProfileRepository
    from afkbot.repositories.run_repo import RunRepository
    from afkbot.repositories.runlog_repo import RunlogRepository
    from afkbot.repositories.subagent_task_repo import SubagentTaskRepository
    from afkbot.repositories.task_flow_repo import TaskFlowRepository
