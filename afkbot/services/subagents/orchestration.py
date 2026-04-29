"""Shared SessionOrchestrator construction for subagent execution paths."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.services.subagents.runtime_policy import (
    DEFAULT_SUBAGENT_RUNTIME_POLICY,
    SubagentRuntimePolicy,
)
from afkbot.settings import Settings

if TYPE_CHECKING:
    from afkbot.services.session_orchestration import SessionOrchestrator


def resolve_subagent_loop_settings(
    *,
    settings: Settings,
    profile_id: str,
    runtime_policy: SubagentRuntimePolicy = DEFAULT_SUBAGENT_RUNTIME_POLICY,
) -> Settings:
    """Resolve effective profile settings and apply child-agent subagent policy."""

    from afkbot.services.agent_loop.runtime_factory import resolve_profile_settings

    profile_settings = resolve_profile_settings(
        settings=settings,
        profile_id=profile_id,
        ensure_layout=True,
    )
    return runtime_policy.build_child_settings(profile_settings)


def build_subagent_session_orchestrator(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    loop_settings: Settings,
    runtime_policy: SubagentRuntimePolicy = DEFAULT_SUBAGENT_RUNTIME_POLICY,
) -> SessionOrchestrator:
    """Build the canonical SessionOrchestrator used by all subagent executions."""

    from afkbot.services.agent_loop.runtime_factory import build_agent_loop_from_settings
    from afkbot.services.session_orchestration import SessionOrchestrator

    def _build_child_runner(loop_session: AsyncSession, child_profile_id: str) -> Any:
        return build_agent_loop_from_settings(
            loop_session,
            settings=loop_settings,
            actor=runtime_policy.actor,
            profile_id=child_profile_id,
        )

    return SessionOrchestrator(
        settings=loop_settings,
        session_factory=session_factory,
        turn_runner_factory=_build_child_runner,
    )
