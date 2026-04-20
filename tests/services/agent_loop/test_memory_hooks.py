"""Tests for AgentLoop automatic scoped memory hooks."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.llm import LLMResponse, MockLLMProvider
from afkbot.services.memory.profile_memory_service import get_profile_memory_service
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


class _MemorySearchStubParams(ToolParameters):
    query: str
    scope: str = "auto"
    limit: int = 5
    include_global: bool = False
    global_limit: int | None = None


class _MemoryUpsertStubParams(ToolParameters):
    memory_key: str
    scope: str = "auto"
    summary: str | None = None
    details_md: str | None = None
    content: str | None = None
    source: str | None = None
    source_kind: str = "manual"
    memory_kind: str = "note"


class _MemorySearchStub(ToolBase):
    name = "memory.search"
    description = "memory search stub"
    parameters_model = _MemorySearchStubParams

    def __init__(self, items: list[dict[str, object]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._items = items or [
            {
                "memory_key": "user-name",
                "summary": "Chat fact: The user name is Nikita.",
                "scope_kind": "chat",
                "memory_kind": "fact",
                "visibility": "local",
                "source_kind": "auto",
                "score": 0.01,
            }
        ]

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params.model_dump()
        self.calls.append(payload)
        return ToolResult(ok=True, payload={"items": list(self._items)})


class _MemoryUpsertStub(ToolBase):
    name = "memory.upsert"
    description = "memory upsert stub"
    parameters_model = _MemoryUpsertStubParams

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params.model_dump()
        self.calls.append(payload)
        return ToolResult(ok=True, payload={"item": {"memory_key": payload.get("memory_key")}})


def _channel_overrides() -> TurnContextOverrides:
    return TurnContextOverrides(
        runtime_metadata={
            "transport": "telegram_user",
            "account_id": "personal-user",
            "peer_id": "100",
            "channel_binding": {"binding_id": "personal-user", "session_policy": "per-chat"},
        }
    )


async def _prepare_db(
    tmp_path: Path,
    db_name: str,
) -> tuple[Settings, AsyncEngine, async_sessionmaker[AsyncSession]]:
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
    return settings, engine, create_session_factory(engine)


async def test_auto_memory_search_adds_runtime_metadata(tmp_path: Path) -> None:
    """Auto-memory search should query scoped memory and inject hits into LLM context."""

    settings, engine, factory = await _prepare_db(tmp_path, "memory_search.db")
    search_tool = _MemorySearchStub()
    upsert_tool = _MemoryUpsertStub()
    llm = MockLLMProvider([LLMResponse.final("finalized: hello")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([search_tool, upsert_tool]),
            llm_provider=llm,
            memory_auto_search_enabled=True,
            memory_auto_search_scope_mode="chat",
            memory_auto_search_include_global=True,
            memory_auto_save_enabled=False,
            memory_auto_search_limit=2,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-1",
            message="What is my name?",
            context_overrides=_channel_overrides(),
        )

        assert result.envelope.action == "finalize"
        assert search_tool.calls
        assert search_tool.calls[0]["scope"] == "chat"
        assert llm.requests
        assert "auto_memory" in llm.requests[0].context
        assert "Nikita" in llm.requests[0].context

    await engine.dispose()


async def test_auto_memory_save_calls_memory_upsert(tmp_path: Path) -> None:
    """Auto-memory save should persist structured scoped memory after finalized turns."""

    settings, engine, factory = await _prepare_db(tmp_path, "memory_save.db")
    search_tool = _MemorySearchStub()
    upsert_tool = _MemoryUpsertStub()

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([search_tool, upsert_tool]),
            memory_auto_search_enabled=False,
            memory_auto_save_enabled=True,
            memory_auto_save_scope_mode="chat",
            memory_auto_save_kinds=("fact", "preference"),
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-1",
            message="В этом чате отвечай коротко",
            context_overrides=_channel_overrides(),
        )

        assert result.envelope.action == "finalize"
        assert upsert_tool.calls
        payload = upsert_tool.calls[0]
        assert str(payload["source"]) == "agent_loop.auto"
        assert payload["scope"] == "chat"
        assert payload["memory_kind"] == "preference"
        assert "В этом чате" in str(payload["summary"])

    await engine.dispose()


async def test_core_profile_memory_is_injected_as_trusted_block(tmp_path: Path) -> None:
    """Pinned core profile memory should render separately from untrusted auto_memory metadata."""

    settings, engine, factory = await _prepare_db(tmp_path, "core_memory.db")
    llm = MockLLMProvider([LLMResponse.final("finalized: hello")])

    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")

    profile_memory = get_profile_memory_service(settings)
    await profile_memory.upsert(
        profile_id="default",
        memory_key="preferred_response_style",
        summary="Reply in Russian and keep answers brief.",
        memory_kind="preference",
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_MemorySearchStub(), _MemoryUpsertStub()]),
            llm_provider=llm,
            memory_core_enabled=True,
            memory_auto_search_enabled=False,
            memory_auto_save_enabled=False,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-1",
            message="Say hello",
            context_overrides=_channel_overrides(),
        )

        assert result.envelope.action == "finalize"
        assert llm.requests
        assert "# Core Memory (trusted)" in llm.requests[0].context
        assert "Reply in Russian and keep answers brief." in llm.requests[0].context
        assert "auto_memory" not in llm.requests[0].context

    await engine.dispose()


async def test_auto_memory_does_not_override_profile_core_memory_trusted_block(
    tmp_path: Path,
) -> None:
    """Auto-memory hits should remain untrusted and not overwrite pinned core memory."""

    settings, engine, factory = await _prepare_db(tmp_path, "core_memory_override.db")
    llm = MockLLMProvider([LLMResponse.final("finalized: hello")])

    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")

    profile_memory = get_profile_memory_service(settings)
    await profile_memory.upsert(
        profile_id="default",
        memory_key="preferred_response_style",
        summary="Reply in Russian and keep answers brief.",
        memory_kind="preference",
    )

    search_tool = _MemorySearchStub(
        items=[
            {
                "memory_key": "preferred_response_style",
                "summary": "Chat preference: Reply in English for this chat.",
                "scope_kind": "chat",
                "memory_kind": "preference",
                "visibility": "local",
                "source_kind": "auto",
                "score": 0.01,
            }
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([search_tool, _MemoryUpsertStub()]),
            llm_provider=llm,
            memory_core_enabled=True,
            memory_auto_search_enabled=True,
            memory_auto_search_scope_mode="chat",
            memory_auto_save_enabled=False,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-1",
            message="How should you answer?",
            context_overrides=_channel_overrides(),
        )

        assert result.envelope.action == "finalize"
        assert llm.requests
        assert "- preferred_response_style: Reply in Russian and keep answers brief." in llm.requests[0].context
        assert "# Runtime Metadata (untrusted)" in llm.requests[0].context
        assert "Reply in English for this chat." in llm.requests[0].context

    await engine.dispose()
