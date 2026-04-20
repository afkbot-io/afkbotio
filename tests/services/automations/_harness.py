"""Shared harness for automation service/runtime tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.automations import AutomationsService
from afkbot.services.automations.graph.executor import AutomationGraphSubagentFactory
from afkbot.settings import Settings


class FakeLoop:
    """Simple fake AgentLoop capturing run_turn calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: TurnContextOverrides | None = None,
        **_unused: object,
    ) -> object:
        self.calls.append(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "message": message,
                "context_overrides": context_overrides,
            }
        )
        return {"ok": True}


class FailingOnceLoop(FakeLoop):
    """Fake loop that fails first run to validate idempotent retry behavior."""

    def __init__(self) -> None:
        super().__init__()
        self._should_fail = True

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: TurnContextOverrides | None = None,
        **_unused: object,
    ) -> object:
        payload = {
            "profile_id": profile_id,
            "session_id": session_id,
            "message": message,
            "context_overrides": context_overrides,
        }
        self.calls.append(payload)
        if self._should_fail:
            self._should_fail = False
            raise RuntimeError("simulated failure after side-effect")
        return {"ok": True}


class BlockingLoop(FakeLoop):
    """Fake loop that blocks until cancelled or explicitly released."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: TurnContextOverrides | None = None,
        **_unused: object,
    ) -> object:
        self.calls.append(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "message": message,
                "context_overrides": context_overrides,
            }
        )
        self.started.set()
        await self.release.wait()
        return {"ok": True}


async def prepare_service(
    tmp_path: Path,
    *,
    graph_subagent_service_factory: AutomationGraphSubagentFactory | None = None,
    settings_override: Settings | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], AutomationsService]:
    """Create one disposable automation service + DB fixture tree."""

    settings = settings_override or Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = AutomationsService(
        factory,
        settings=settings,
        graph_subagent_service_factory=graph_subagent_service_factory,
    )
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        await profiles.get_or_create_default("other")
    return engine, factory, service
