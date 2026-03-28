"""Lazy exports for agent-loop public surface."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentLoop",
    "CanonicalProgressStage",
    "ProgressCursor",
    "ProgressEvent",
    "ProgressStream",
    "SecurityGuard",
]


def __getattr__(name: str) -> Any:
    """Resolve heavy agent-loop exports lazily to avoid package import cycles."""

    if name == "AgentLoop":
        from afkbot.services.agent_loop.loop import AgentLoop

        return AgentLoop
    if name == "SecurityGuard":
        from afkbot.services.agent_loop.security_guard import SecurityGuard

        return SecurityGuard
    if name in {
        "CanonicalProgressStage",
        "ProgressCursor",
        "ProgressEvent",
        "ProgressStream",
    }:
        module = import_module("afkbot.services.agent_loop.progress_stream")
        return getattr(module, name)
    raise AttributeError(name)
