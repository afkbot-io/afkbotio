"""AgentLoop factory helper for automation runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class AgentLoopLike(Protocol):
    """Minimal AgentLoop contract used by automation trigger execution."""

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: object | None = None,
    ) -> object: ...


def build_automation_agent_loop(
    *,
    agent_loop_factory: Callable[..., AgentLoopLike],
    session: AsyncSession,
    profile_id: str,
) -> AgentLoopLike:
    """Build AgentLoop from the explicit automation factory contract."""

    return agent_loop_factory(session, profile_id)
