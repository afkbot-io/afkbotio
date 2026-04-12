"""Session turn runner factory helper for automation runtime."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.services.session_orchestration import SessionOrchestrator, SessionTurnRunner
from afkbot.settings import Settings

AutomationSessionRunnerFactory = Callable[
    [async_sessionmaker[AsyncSession], str],
    SessionTurnRunner,
]


def build_automation_session_runner(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    settings: Settings,
    runner_factory: AutomationSessionRunnerFactory | None,
) -> SessionTurnRunner:
    """Build the session-level runner used by automation trigger execution."""

    if runner_factory is not None:
        return runner_factory(session_factory, profile_id)
    return SessionOrchestrator(settings=settings, session_factory=session_factory)
