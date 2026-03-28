"""Shared helpers for agent loop integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory
from afkbot.services.llm import BaseLLMProvider, LLMResponse
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class SlowTool(ToolBase):
    """Test tool that sleeps long enough to exercise cancellation paths."""

    name = "debug.slow"
    description = "slow tool"
    parameters_model = ToolParameters

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        await asyncio.sleep(1.0)
        return ToolResult(ok=True, payload={"done": True})


class SleepLLMProvider(BaseLLMProvider):
    """LLM provider stub that sleeps before returning a fixed response."""

    def __init__(self, *, sleep_sec: float, response: LLMResponse) -> None:
        self._sleep_sec = sleep_sec
        self._response = response

    async def complete(self, request: object) -> LLMResponse:
        _ = request
        await asyncio.sleep(self._sleep_sec)
        return self._response


async def create_test_db(
    tmp_path: Path,
    db_name: str,
) -> tuple[Settings, AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create isolated SQLite runtime with minimal bootstrap and synthetic skill files."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    (skills_dir / "SKILL.md").write_text("# security-secrets", encoding="utf-8")

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    return settings, engine, factory
