"""Tests for hybrid chat transcript storage backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_compaction_repo import ChatSessionCompactionRepository
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_turn_repo import ChatTurnRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.chat_history_builder import ChatHistoryBuilder
from afkbot.services.agent_loop.session_compaction import SessionCompactionService
from afkbot.services.session_transcripts import (
    DatabaseChatTranscriptStore,
    DualChatTranscriptStore,
    JsonlChatTranscriptStore,
)
from afkbot.settings import Settings


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_lists_recent_and_ranges(tmp_path: Path) -> None:
    store = JsonlChatTranscriptStore(root_dir=tmp_path)

    first = await store.create_turn(
        profile_id="default",
        session_id="s-1",
        user_message="hello",
        assistant_message="hi",
    )
    second = await store.create_turn(
        profile_id="default",
        session_id="s-1",
        user_message="plan",
        assistant_message="done",
    )

    assert first.id == 1
    assert second.id == 2
    assert await store.count(profile_id="default", session_id="s-1") == 2
    recent = await store.list_recent(profile_id="default", session_id="s-1", limit=1)
    assert [row.user_message for row in recent] == ["plan"]
    ranged = await store.list_range(
        profile_id="default",
        session_id="s-1",
        min_turn_id_exclusive=first.id,
    )
    assert [row.assistant_message for row in ranged] == ["done"]


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_prunes_with_tombstone(tmp_path: Path) -> None:
    store = JsonlChatTranscriptStore(root_dir=tmp_path)

    for index in range(4):
        await store.create_turn(
            profile_id="default",
            session_id="s-prune",
            user_message=f"user-{index}",
            assistant_message=f"assistant-{index}",
        )

    deleted = await store.delete_up_to(
        profile_id="default",
        session_id="s-prune",
        max_turn_id_inclusive=3,
        limit=2,
    )

    assert deleted == 2
    rows = await store.list_recent(profile_id="default", session_id="s-prune", limit=10)
    assert [row.id for row in rows] == [3, 4]


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_skips_corrupt_partial_line(tmp_path: Path) -> None:
    store = JsonlChatTranscriptStore(root_dir=tmp_path)
    await store.create_turn(
        profile_id="default",
        session_id="s-corrupt",
        user_message="valid",
        assistant_message="ok",
    )

    data_file = next((tmp_path / "session_transcripts").glob("*/*.jsonl"))
    with data_file.open("a", encoding="utf-8") as handle:
        handle.write('{"op": "turn", "id":')

    rows = await store.list_recent(profile_id="default", session_id="s-corrupt", limit=10)
    assert [row.user_message for row in rows] == ["valid"]


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_defers_append_until_db_commit(tmp_path: Path) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'transcripts_defer.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-defer",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            assert await ProfileRepository(session).get("default") is not None
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            row = await store.create_turn(
                profile_id="default",
                session_id="s-defer",
                user_message="pending",
                assistant_message="commit",
            )
            assert row.id == 1
            assert list((tmp_path / "session_transcripts").glob("*/*.jsonl")) == []
        assert list((tmp_path / "session_transcripts").glob("*/*.jsonl"))

        async with session_scope(factory) as session:
            assert await ProfileRepository(session).get("default") is not None
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            await store.create_turn(
                profile_id="default",
                session_id="s-rollback",
                user_message="rollback",
                assistant_message="skip",
            )
            await session.rollback()
        rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-rollback",
            limit=10,
        )
        assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dual_chat_transcript_create_rolls_back_without_jsonl_leak(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'dual_create_rollback.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-dual-create-rollback",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            store = DualChatTranscriptStore(
                primary=DatabaseChatTranscriptStore(ChatTurnRepository(session)),
                mirror=JsonlChatTranscriptStore(root_dir=tmp_path, session=session),
            )
            await store.create_turn(
                profile_id="default",
                session_id="s-dual-create-rollback",
                user_message="pending",
                assistant_message="rollback",
            )
            await session.rollback()

        jsonl_rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-dual-create-rollback",
            limit=10,
        )
        async with session_scope(factory) as session:
            db_rows = await DatabaseChatTranscriptStore(ChatTurnRepository(session)).list_recent(
                profile_id="default",
                session_id="s-dual-create-rollback",
                limit=10,
            )

        assert jsonl_rows == []
        assert db_rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_repairs_failed_post_commit_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'transcripts_repair.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-repair",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)

            def _raise_apply(_mutation: dict[str, object]) -> None:
                raise OSError("simulated append failure")

            monkeypatch.setattr(store, "_apply_mutation_sync", _raise_apply)
            await store.create_turn(
                profile_id="default",
                session_id="s-repair",
                user_message="pending",
                assistant_message="committed",
            )

        assert list((tmp_path / "session_transcripts").glob("*/*.jsonl")) == []

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            rows = await store.list_recent(
                profile_id="default",
                session_id="s-repair",
                limit=10,
            )

        assert [row.user_message for row in rows] == ["pending"]
        assert list((tmp_path / "session_transcripts").glob("*/*.jsonl"))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_replays_all_committed_outbox_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'transcripts_repair_many.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-repair-many",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)

            def _raise_apply(_mutation: dict[str, object]) -> None:
                raise OSError("simulated append failure")

            monkeypatch.setattr(store, "_apply_mutation_sync", _raise_apply)
            for index in range(105):
                await store.create_turn(
                    profile_id="default",
                    session_id="s-repair-many",
                    user_message=f"user-{index}",
                    assistant_message=f"assistant-{index}",
                )

        assert list((tmp_path / "session_transcripts").glob("*/*.jsonl")) == []

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            rows = await store.list_recent(
                profile_id="default",
                session_id="s-repair-many",
                limit=200,
            )

        assert len(rows) == 105
        assert rows[-1].user_message == "user-104"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_keeps_order_after_interleaved_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'transcripts_order.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-order",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)

            def _raise_apply(_mutation: dict[str, object]) -> None:
                raise OSError("simulated append failure")

            monkeypatch.setattr(store, "_apply_mutation_sync", _raise_apply)
            await store.create_turn(
                profile_id="default",
                session_id="s-order",
                user_message="u1",
                assistant_message="a1",
            )

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            await store.create_turn(
                profile_id="default",
                session_id="s-order",
                user_message="u2",
                assistant_message="a2",
            )

        async with session_scope(factory) as session:
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            rows = await store.list_recent(
                profile_id="default",
                session_id="s-order",
                limit=10,
            )

        assert [(row.id, row.user_message) for row in rows] == [(1, "u1"), (2, "u2")]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dual_chat_transcript_prune_tombstone_rolls_back_with_db_transaction(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'dual_prune.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-dual-prune",
                profile_id="default",
            )
            store = DualChatTranscriptStore(
                primary=DatabaseChatTranscriptStore(ChatTurnRepository(session)),
                mirror=JsonlChatTranscriptStore(root_dir=tmp_path, session=session),
            )
            for index in range(2):
                await store.create_turn(
                    profile_id="default",
                    session_id="s-dual-prune",
                    user_message=f"user-{index}",
                    assistant_message=f"assistant-{index}",
                )

        async with session_scope(factory) as session:
            store = DualChatTranscriptStore(
                primary=DatabaseChatTranscriptStore(ChatTurnRepository(session)),
                mirror=JsonlChatTranscriptStore(root_dir=tmp_path, session=session),
            )
            deleted = await store.delete_up_to(
                profile_id="default",
                session_id="s-dual-prune",
                max_turn_id_inclusive=1,
                limit=1,
            )
            assert deleted == 1
            await session.rollback()

        async with session_scope(factory) as session:
            db_store = DatabaseChatTranscriptStore(ChatTurnRepository(session))
            db_rows = await db_store.list_recent(
                profile_id="default",
                session_id="s-dual-prune",
                limit=10,
            )
        jsonl_rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-dual-prune",
            limit=10,
        )

        assert [row.id for row in db_rows] == [1, 2]
        assert [row.id for row in jsonl_rows] == [1, 2]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dual_chat_transcript_mirror_preserves_db_turn_ids_for_jsonl_cutover(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'dual_cutover.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="other-session",
                profile_id="default",
            )
            await ChatSessionRepository(session).create(
                session_id="s-cutover",
                profile_id="default",
            )
            await ChatTurnRepository(session).create(
                profile_id="default",
                session_id="other-session",
                user_message="other",
                assistant_message="other",
            )
            store = DualChatTranscriptStore(
                primary=DatabaseChatTranscriptStore(ChatTurnRepository(session)),
                mirror=JsonlChatTranscriptStore(root_dir=tmp_path, session=session),
            )
            first = await store.create_turn(
                profile_id="default",
                session_id="s-cutover",
                user_message="u1",
                assistant_message="a1",
            )
            second = await store.create_turn(
                profile_id="default",
                session_id="s-cutover",
                user_message="u2",
                assistant_message="a2",
            )
            assert first.id > 1
            await ChatSessionCompactionRepository(session).upsert(
                profile_id="default",
                session_id="s-cutover",
                summary_text="summary before cutover",
                compacted_until_turn_id=first.id,
                source_turn_count=1,
                strategy="deterministic_v1",
            )

        async with session_scope(factory) as session:
            third = await JsonlChatTranscriptStore(
                root_dir=tmp_path,
                session=session,
            ).create_turn(
                profile_id="default",
                session_id="s-cutover",
                user_message="u3",
                assistant_message="a3",
            )

        async with session_scope(factory) as session:
            jsonl_store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            compaction = SessionCompactionService(
                session,
                enabled=True,
                trigger_turns=999,
                keep_recent_turns=10,
                history_turns=10,
                max_chars=1000,
                transcript_store=jsonl_store,
            )
            history = await ChatHistoryBuilder(
                session=session,
                history_turns=10,
                sanitize=lambda value: value,
                session_compaction=compaction,
                transcript_store=jsonl_store,
            ).build(
                profile_id="default",
                session_id="s-cutover",
                user_message="current",
            )

        mirrored_rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-cutover",
            limit=10,
        )
        assert [row.id for row in mirrored_rows] == [first.id, second.id, third.id]
        assert third.id > second.id
        assert [message.content for message in history if message.role == "user"] == [
            "u2",
            "u3",
            "current",
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_backfills_existing_database_turns_for_cutover(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'db_to_jsonl.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-db-cutover",
                profile_id="default",
            )
            repo = ChatTurnRepository(session)
            first = await repo.create(
                profile_id="default",
                session_id="s-db-cutover",
                user_message="db-u1",
                assistant_message="db-a1",
            )
            second = await repo.create(
                profile_id="default",
                session_id="s-db-cutover",
                user_message="db-u2",
                assistant_message="db-a2",
            )
            await ChatSessionCompactionRepository(session).upsert(
                profile_id="default",
                session_id="s-db-cutover",
                summary_text="db summary",
                compacted_until_turn_id=first.id,
                source_turn_count=1,
                strategy="deterministic_v1",
            )

        async with session_scope(factory) as session:
            jsonl_store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            third = await jsonl_store.create_turn(
                profile_id="default",
                session_id="s-db-cutover",
                user_message="jsonl-u3",
                assistant_message="jsonl-a3",
            )

        async with session_scope(factory) as session:
            jsonl_store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            compaction = SessionCompactionService(
                session,
                enabled=True,
                trigger_turns=999,
                keep_recent_turns=10,
                history_turns=10,
                max_chars=1000,
                transcript_store=jsonl_store,
            )
            history = await ChatHistoryBuilder(
                session=session,
                history_turns=10,
                sanitize=lambda value: value,
                session_compaction=compaction,
                transcript_store=jsonl_store,
            ).build(
                profile_id="default",
                session_id="s-db-cutover",
                user_message="current",
            )

        rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-db-cutover",
            limit=10,
        )
        assert [(row.id, row.user_message) for row in rows] == [
            (first.id, "db-u1"),
            (second.id, "db-u2"),
            (third.id, "jsonl-u3"),
        ]
        assert third.id > second.id
        assert [message.content for message in history if message.role == "user"] == [
            "db-u2",
            "jsonl-u3",
            "current",
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_does_not_materialize_uncommitted_peer_outbox(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'transcripts_peer.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-peer",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            writer = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            reader = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            await writer.create_turn(
                profile_id="default",
                session_id="s-peer",
                user_message="pending",
                assistant_message="not-yet",
            )

            rows = await reader.list_recent(
                profile_id="default",
                session_id="s-peer",
                limit=10,
            )

            assert rows == []
            assert list((tmp_path / "session_transcripts").glob("*/*.jsonl")) == []

        rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-peer",
            limit=10,
        )
        assert [row.user_message for row in rows] == ["pending"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jsonl_chat_transcript_store_continues_legacy_file_sequence_with_db_outbox(
    tmp_path: Path,
) -> None:
    legacy_store = JsonlChatTranscriptStore(root_dir=tmp_path)
    await legacy_store.create_turn(
        profile_id="default",
        session_id="s-legacy-jsonl",
        user_message="legacy-1",
        assistant_message="ok-1",
    )
    await legacy_store.create_turn(
        profile_id="default",
        session_id="s-legacy-jsonl",
        user_message="legacy-2",
        assistant_message="ok-2",
    )

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_jsonl.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-legacy-jsonl",
                profile_id="default",
            )
            store = JsonlChatTranscriptStore(root_dir=tmp_path, session=session)
            row = await store.create_turn(
                profile_id="default",
                session_id="s-legacy-jsonl",
                user_message="new-3",
                assistant_message="ok-3",
            )

        rows = await JsonlChatTranscriptStore(root_dir=tmp_path).list_recent(
            profile_id="default",
            session_id="s-legacy-jsonl",
            limit=10,
        )
        assert row.id == 3
        assert [(item.id, item.user_message) for item in rows] == [
            (1, "legacy-1"),
            (2, "legacy-2"),
            (3, "new-3"),
        ]
    finally:
        await engine.dispose()
