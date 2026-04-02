"""Tests for trusted session compaction and pruning."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.chat_session_compaction import ChatSessionCompaction
from afkbot.models.chat_turn import ChatTurn
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.session_compaction import SessionCompactionService
from afkbot.services.agent_loop.session_retention import SessionRetentionService
from afkbot.services.llm import LLMResponse, MockLLMProvider
from afkbot.services.skills.skills import SkillLoader
from afkbot.settings import Settings


async def test_session_compaction_refreshes_incrementally(tmp_path: Path) -> None:
    """Compaction should summarize only older turns and append new compactable turns later."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'session_compaction.db'}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-1", profile_id="default")
        session.add_all(
            [
                ChatTurn(profile_id="default", session_id="s-1", user_message="u1", assistant_message="a1"),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u2", assistant_message="a2"),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u3", assistant_message="a3"),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u4", assistant_message="a4"),
            ]
        )
        await session.flush()

        service = SessionCompactionService(
            session,
            enabled=True,
            trigger_turns=3,
            keep_recent_turns=2,
            history_turns=4,
            max_chars=1000,
        )

        first = await service.refresh_if_needed(profile_id="default", session_id="s-1")
        snapshot = await service.load_snapshot(profile_id="default", session_id="s-1")

        assert first == type(first)(
            updated=True,
            compacted_until_turn_id=2,
            source_turn_count=2,
            new_turn_count=2,
        )
        assert snapshot is not None
        assert snapshot.compacted_until_turn_id == 2
        assert "- [T1] User: u1" in snapshot.summary_text
        assert "- [T2] User: u2" in snapshot.summary_text
        assert "- [T3] User: u3" not in snapshot.summary_text
        remaining_after_first = await session.scalars(
            select(ChatTurn).where(
                ChatTurn.profile_id == "default",
                ChatTurn.session_id == "s-1",
            )
        )
        assert [row.id for row in remaining_after_first] == [1, 2, 3, 4]

        session.add(ChatTurn(profile_id="default", session_id="s-1", user_message="u5", assistant_message="a5"))
        await session.flush()

        second = await service.refresh_if_needed(profile_id="default", session_id="s-1")
        snapshot = await service.load_snapshot(profile_id="default", session_id="s-1")

        assert second == type(second)(
            updated=True,
            compacted_until_turn_id=3,
            source_turn_count=3,
            new_turn_count=1,
        )
        assert snapshot is not None
        assert "- [T3] User: u3" in snapshot.summary_text
        remaining_after_second = await session.scalars(
            select(ChatTurn).where(
                ChatTurn.profile_id == "default",
                ChatTurn.session_id == "s-1",
            )
        )
        assert [row.id for row in remaining_after_second] == [1, 2, 3, 4, 5]

        retention = SessionRetentionService(
            session,
            prune_raw_turns=True,
            gc_batch_size=2,
        )
        first_gc = await retention.garbage_collect_session(profile_id="default", session_id="s-1")
        second_gc = await retention.garbage_collect_session(profile_id="default", session_id="s-1")
        remaining_after_gc = await session.scalars(
            select(ChatTurn).where(
                ChatTurn.profile_id == "default",
                ChatTurn.session_id == "s-1",
            )
        )

        assert first_gc.deleted_turn_count == 2
        assert second_gc.deleted_turn_count == 1
        assert [row.id for row in remaining_after_gc] == [4, 5]

    await engine.dispose()


async def test_session_compaction_uses_llm_merge_when_summary_budget_is_tight(tmp_path: Path) -> None:
    """Large persisted summaries should switch to hybrid LLM merge before pruning too aggressively."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'session_compaction_llm.db'}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    llm = MockLLMProvider([LLMResponse.final("Goal: keep context compact\nNext: continue with turn three")])

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-1", profile_id="default")
        session.add_all(
            [
                ChatTurn(profile_id="default", session_id="s-1", user_message="u1 " * 40, assistant_message="a1 " * 40),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u2 " * 40, assistant_message="a2 " * 40),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u3 " * 40, assistant_message="a3 " * 40),
            ]
        )
        await session.flush()

        service = SessionCompactionService(
            session,
            enabled=True,
            trigger_turns=2,
            keep_recent_turns=1,
            history_turns=3,
            max_chars=140,
            llm_provider=llm,
        )

        result = await service.refresh_if_needed(profile_id="default", session_id="s-1")
        snapshot = await service.load_snapshot(profile_id="default", session_id="s-1")

        assert result.updated is True
        assert snapshot is not None
        assert snapshot.strategy == "hybrid_llm_v1"
        assert "Goal: keep context compact" in snapshot.summary_text
        assert len(llm.requests) == 1

    await engine.dispose()


async def test_agent_loop_uses_compacted_summary_in_followup_history(tmp_path: Path) -> None:
    """Longer sessions should switch older turns from raw replay to trusted compact summary."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    (skills_dir / "SKILL.md").write_text("# security-secrets", encoding="utf-8")

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'session_compaction_loop.db'}",
        root_dir=tmp_path,
        session_compaction_enabled=True,
        session_compaction_trigger_turns=2,
        session_compaction_keep_recent_turns=1,
        session_compaction_prune_raw_turns=True,
        llm_history_turns=2,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    llm = MockLLMProvider(
        [
            LLMResponse.final("assistant-1"),
            LLMResponse.final("assistant-2"),
            LLMResponse.final("assistant-3"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            llm_provider=llm,
            session_compaction_enabled=True,
            session_compaction_trigger_turns=2,
            session_compaction_keep_recent_turns=1,
            session_compaction_prune_raw_turns=True,
            llm_history_turns=2,
        )

        await loop.run_turn(profile_id="default", session_id="s-1", message="first request")
        await loop.run_turn(profile_id="default", session_id="s-1", message="second request")
        await loop.run_turn(profile_id="default", session_id="s-1", message="third request")

        assert len(llm.requests) == 3
        third_history = [(item.role, item.content) for item in llm.requests[2].history]
        assert third_history == [
            (
                "system",
                "Trusted compact session summary for earlier turns. "
                "The full raw transcript before this boundary was pruned from history.\n"
                "Compacted through turn 1.\n"
                "- [T1] User: first request\n"
                "  Assistant: assistant-1",
            ),
            ("user", "second request"),
            ("assistant", "assistant-2"),
            ("user", "third request"),
        ]
        compacted = await session.scalars(
            select(ChatSessionCompaction).where(
                ChatSessionCompaction.profile_id == "default",
                ChatSessionCompaction.session_id == "s-1",
            )
        )
        compacted_row = compacted.one()
        remaining_turns = await session.scalars(
            select(ChatTurn).where(
                ChatTurn.profile_id == "default",
                ChatTurn.session_id == "s-1",
            )
        )
        assert compacted_row.compacted_until_turn_id == 2
        assert [row.id for row in remaining_turns] == [3]

    await engine.dispose()


async def test_session_retention_garbage_collects_profile_in_bounded_batch(tmp_path: Path) -> None:
    """Profile GC should prune compacted raw turns across sessions without unbounded deletes."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'session_retention.db'}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        for session_id in ("s-1", "s-2"):
            await ChatSessionRepository(session).create(session_id=session_id, profile_id="default")
        session.add_all(
            [
                ChatTurn(profile_id="default", session_id="s-1", user_message="u1", assistant_message="a1"),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u2", assistant_message="a2"),
                ChatTurn(profile_id="default", session_id="s-1", user_message="u3", assistant_message="a3"),
                ChatTurn(profile_id="default", session_id="s-2", user_message="u4", assistant_message="a4"),
                ChatTurn(profile_id="default", session_id="s-2", user_message="u5", assistant_message="a5"),
                ChatTurn(profile_id="default", session_id="s-2", user_message="u6", assistant_message="a6"),
            ]
        )
        await session.flush()
        compaction = SessionCompactionService(
            session,
            enabled=True,
            trigger_turns=2,
            keep_recent_turns=1,
            history_turns=3,
            max_chars=1000,
        )
        await compaction.refresh_if_needed(profile_id="default", session_id="s-1")
        await compaction.refresh_if_needed(profile_id="default", session_id="s-2")

        retention = SessionRetentionService(session, prune_raw_turns=True, gc_batch_size=2)
        gc_result = await retention.garbage_collect_profile(profile_id="default")

        remaining = await session.scalars(
            select(ChatTurn).where(ChatTurn.profile_id == "default").order_by(ChatTurn.id.asc())
        )

        assert gc_result.deleted_turn_count == 2
        assert gc_result.scanned_session_count == 2
        assert [row.id for row in remaining] == [3, 4, 5, 6]

    await engine.dispose()
