"""Tests for the chat history builder."""

from __future__ import annotations
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.chat_turn import ChatTurn
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.chat_history_builder import ChatHistoryBuilder
from afkbot.services.agent_loop.session_compaction import SessionCompactionService
from afkbot.settings import Settings


async def _prepare_db(
    tmp_path: Path,
    db_name: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    return engine, create_session_factory(engine)


async def test_chat_history_builder_builds_sanitized_history_in_order(tmp_path: Path) -> None:
    """Builder should return the latest persisted turns in chronological order plus current input."""

    engine, factory = await _prepare_db(tmp_path, "chat_history.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-1", profile_id="default")
        session.add_all(
            [
                ChatTurn(
                    profile_id="default",
                    session_id="s-1",
                    user_message="first secret",
                    assistant_message="reply one",
                ),
                ChatTurn(
                    profile_id="default",
                    session_id="s-1",
                    user_message="second user",
                    assistant_message="second secret",
                ),
                ChatTurn(
                    profile_id="default",
                    session_id="s-1",
                    user_message="third user",
                    assistant_message="reply three",
                ),
            ]
        )
        await session.flush()

        builder = ChatHistoryBuilder(
            session=session,
            history_turns=2,
            sanitize=lambda text: text.replace("secret", "[redacted]"),
            session_compaction=SessionCompactionService(
                session,
                enabled=False,
                trigger_turns=3,
                keep_recent_turns=2,
                history_turns=2,
                max_chars=1000,
            ),
        )
        history = await builder.build(
            profile_id="default",
            session_id="s-1",
            user_message="current secret",
        )

        assert [(item.role, item.content) for item in history] == [
            ("user", "second user"),
            ("assistant", "second [redacted]"),
            ("user", "third user"),
            ("assistant", "reply three"),
            ("user", "current secret"),
        ]

    await engine.dispose()


async def test_chat_history_builder_includes_compacted_summary_before_recent_turns(
    tmp_path: Path,
) -> None:
    """Builder should prepend trusted session summary and skip already compacted turns."""

    engine, factory = await _prepare_db(tmp_path, "chat_history_compacted.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-1", profile_id="default")
        session.add_all(
            [
                ChatTurn(
                    profile_id="default",
                    session_id="s-1",
                    user_message="first question",
                    assistant_message="first answer",
                ),
                ChatTurn(
                    profile_id="default",
                    session_id="s-1",
                    user_message="second secret",
                    assistant_message="second answer",
                ),
                ChatTurn(
                    profile_id="default",
                    session_id="s-1",
                    user_message="third user",
                    assistant_message="third reply",
                ),
            ]
        )
        await session.flush()

        compaction = SessionCompactionService(
            session,
            enabled=True,
            trigger_turns=2,
            keep_recent_turns=1,
            history_turns=2,
            max_chars=1000,
        )
        result = await compaction.refresh_if_needed(profile_id="default", session_id="s-1")
        assert result.updated is True

        builder = ChatHistoryBuilder(
            session=session,
            history_turns=2,
            sanitize=lambda text: text.replace("secret", "[redacted]"),
            session_compaction=compaction,
        )
        history = await builder.build(
            profile_id="default",
            session_id="s-1",
            user_message="current secret",
        )

        assert [(item.role, item.content) for item in history] == [
            (
                "system",
                "Trusted compact session summary for earlier turns. "
                "The full raw transcript before this boundary was pruned from history.\n"
                "Compacted through turn 2.\n"
                "- [T1] User: first question\n"
                "  Assistant: first answer\n"
                "- [T2] User: second [redacted]\n"
                "  Assistant: second answer",
            ),
            ("user", "third user"),
            ("assistant", "third reply"),
            ("user", "current secret"),
        ]

    await engine.dispose()
