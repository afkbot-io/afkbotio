"""Integration tests for explicit conversation recall tool plugins."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.chat_turn import ChatTurn
from afkbot.repositories.chat_session_compaction_repo import ChatSessionCompactionRepository
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.memory import reset_memory_services
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


def _user_facing_ctx(*, session_id: str = "chat:100") -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id=session_id,
        run_id=1,
        runtime_metadata={
            "transport": "telegram_user",
            "account_id": "personal-user",
            "peer_id": session_id.split(":")[-1],
            "channel_binding": {"binding_id": "personal-user", "session_policy": "per-chat"},
        },
    )


def _trusted_ctx() -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id="cli:memory",
        run_id=2,
        runtime_metadata={"transport": "cli"},
    )


def _metadata_missing_transport_ctx(*, session_id: str = "chat:100") -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id=session_id,
        run_id=3,
        runtime_metadata={"account_id": "personal-user", "peer_id": session_id.split(":")[-1]},
    )


async def _prepare(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> tuple[Settings, ToolRegistry]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'conversation_recall.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    monkeypatch.setenv("AFKBOT_MEMORY_RECALL_ENABLED", "1")
    get_settings.cache_clear()
    reset_memory_services()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        sessions = ChatSessionRepository(session)
        await sessions.create(session_id="chat:100", profile_id="default")
        await sessions.create(session_id="chat:200", profile_id="default")
        session.add_all(
            [
                ChatTurn(
                    session_id="chat:100",
                    profile_id="default",
                    user_message="We discussed Redis cache rollout.",
                    assistant_message="Noted the Redis cache rollout.",
                ),
                ChatTurn(
                    session_id="chat:100",
                    profile_id="default",
                    user_message="Then we compared Redis cache and Memcached.",
                    assistant_message="Compared Redis cache and Memcached tradeoffs.",
                ),
                ChatTurn(
                    session_id="chat:100",
                    profile_id="default",
                    user_message="Recent update: keep Postgres as the source of truth.",
                    assistant_message="Postgres remains the source of truth.",
                ),
                ChatTurn(
                    session_id="chat:100",
                    profile_id="default",
                    user_message="Latest note: Redis cache still helps read throughput.",
                    assistant_message="Redis cache helps read throughput.",
                ),
                ChatTurn(
                    session_id="chat:200",
                    profile_id="default",
                    user_message="Foreign session note about invoice approval.",
                    assistant_message="Invoice approval is still pending.",
                ),
            ]
        )
        await session.flush()
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:100",
            summary_text="Earlier summary: we decided Redis cache is useful for hot reads.",
            compacted_until_turn_id=2,
            source_turn_count=2,
            strategy="deterministic_v1",
        )

    await engine.dispose()
    return settings, ToolRegistry.from_settings(settings)


async def test_conversation_recall_uses_compaction_and_recent_tail_without_duplicates(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, registry = await _prepare(tmp_path, monkeypatch)
    recall_tool = registry.get("memory.recall.search")
    assert recall_tool is not None

    result = await recall_tool.execute(
        _user_facing_ctx(),
        recall_tool.parse_params(
            {"profile_key": "default", "query": "Redis cache", "limit": 5},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert result.ok is True
    items = result.payload["items"]
    assert isinstance(items, list)
    assert [item["kind"] for item in items] == ["compaction", "turn"]
    assert all(item.get("turn_id") is None or int(item["turn_id"]) > 2 for item in items)
    assert "Redis cache" in str(items[0]["excerpt"])


async def test_user_facing_conversation_recall_cannot_access_foreign_session(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, registry = await _prepare(tmp_path, monkeypatch)
    recall_tool = registry.get("memory.recall.search")
    assert recall_tool is not None

    result = await recall_tool.execute(
        _user_facing_ctx(session_id="chat:100"),
        recall_tool.parse_params(
            {"profile_key": "default", "session_id": "chat:200", "query": "invoice"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert result.ok is False
    assert result.error_code == "memory_cross_scope_forbidden"


async def test_conversation_recall_fails_closed_when_transport_metadata_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, registry = await _prepare(tmp_path, monkeypatch)
    recall_tool = registry.get("memory.recall.search")
    assert recall_tool is not None

    result = await recall_tool.execute(
        _metadata_missing_transport_ctx(session_id="chat:100"),
        recall_tool.parse_params(
            {"profile_key": "default", "session_id": "chat:200", "query": "invoice"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert result.ok is False
    assert result.error_code == "memory_cross_scope_forbidden"


async def test_trusted_conversation_recall_can_target_foreign_session_explicitly(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, registry = await _prepare(tmp_path, monkeypatch)
    recall_tool = registry.get("memory.recall.search")
    assert recall_tool is not None

    result = await recall_tool.execute(
        _trusted_ctx(),
        recall_tool.parse_params(
            {"profile_key": "default", "session_id": "chat:200", "query": "invoice"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert result.ok is True
    items = result.payload["items"]
    assert isinstance(items, list)
    assert items[0]["session_id"] == "chat:200"
    assert items[0]["kind"] == "turn"


async def test_recent_exact_turn_can_outrank_weaker_compaction_match(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, registry = await _prepare(tmp_path, monkeypatch)
    recall_tool = registry.get("memory.recall.search")
    assert recall_tool is not None

    result = await recall_tool.execute(
        _user_facing_ctx(),
        recall_tool.parse_params(
            {"profile_key": "default", "query": "source of truth", "limit": 2},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert result.ok is True
    items = result.payload["items"]
    assert isinstance(items, list)
    assert items[0]["kind"] == "turn"
    assert "source of truth" in str(items[0]["excerpt"]).lower()
