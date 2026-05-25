"""Append-heavy chat transcript storage backends."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Literal, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import add_after_commit_callback, create_peer_session_factory
from afkbot.db.sqlite_lock import sqlite_file_lock_sync
from afkbot.repositories.chat_turn_repo import ChatTurnRepository
from afkbot.services.session_jsonl import (
    JsonlSessionPaths,
    jsonl_paths_for_session,
    next_jsonl_sequence_id,
)
from afkbot.services.session_jsonl_outbox import (
    allocate_session_jsonl_id,
    drain_session_jsonl_outbox,
    ensure_session_jsonl_next_id,
    enqueue_session_jsonl_mutation,
)
from afkbot.settings import Settings

SessionTranscriptStoreBackend = Literal["database", "jsonl", "dual"]
_TRANSCRIPT_NAMESPACE = "session_transcripts"


class ChatTurnRecord(Protocol):
    """Read model consumed by history and compaction services."""

    @property
    def id(self) -> int: ...

    @property
    def session_id(self) -> str: ...

    @property
    def profile_id(self) -> str: ...

    @property
    def user_message(self) -> str: ...

    @property
    def assistant_message(self) -> str: ...


class ChatTranscriptStore(Protocol):
    """Storage interface for persisted chat turns."""

    async def create_turn(
        self,
        *,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ChatTurnRecord:
        """Persist one chat turn and return its read model."""

    async def count(self, *, profile_id: str, session_id: str) -> int:
        """Return total retained turn count for one session."""

    async def list_recent(
        self,
        *,
        profile_id: str,
        session_id: str,
        limit: int,
        min_turn_id_exclusive: int = 0,
    ) -> list[ChatTurnRecord]:
        """Return recent retained turns in chronological order."""

    async def list_range(
        self,
        *,
        profile_id: str,
        session_id: str,
        min_turn_id_exclusive: int = 0,
        max_turn_id_exclusive: int | None = None,
    ) -> list[ChatTurnRecord]:
        """Return retained turns in ascending order within one id window."""

    async def delete_up_to(
        self,
        *,
        profile_id: str,
        session_id: str,
        max_turn_id_inclusive: int,
        limit: int | None = None,
    ) -> int:
        """Prune retained turns through the provided boundary."""


@dataclass(frozen=True, slots=True)
class JsonlChatTurnRead:
    """Chat turn read model loaded from the JSONL transcript store."""

    id: int
    session_id: str
    profile_id: str
    user_message: str
    assistant_message: str
    created_at: datetime


@dataclass(slots=True)
class DatabaseChatTranscriptStore:
    """Chat transcript storage backed by the relational runtime database."""

    repository: ChatTurnRepository

    async def create_turn(
        self,
        *,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ChatTurnRecord:
        return await self.repository.create(
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )

    async def count(self, *, profile_id: str, session_id: str) -> int:
        return await self.repository.count(profile_id=profile_id, session_id=session_id)

    async def list_recent(
        self,
        *,
        profile_id: str,
        session_id: str,
        limit: int,
        min_turn_id_exclusive: int = 0,
    ) -> list[ChatTurnRecord]:
        rows = await self.repository.list_recent(
            profile_id=profile_id,
            session_id=session_id,
            limit=limit,
            min_turn_id_exclusive=min_turn_id_exclusive,
        )
        return cast(list[ChatTurnRecord], rows)

    async def list_range(
        self,
        *,
        profile_id: str,
        session_id: str,
        min_turn_id_exclusive: int = 0,
        max_turn_id_exclusive: int | None = None,
    ) -> list[ChatTurnRecord]:
        rows = await self.repository.list_range(
            profile_id=profile_id,
            session_id=session_id,
            min_turn_id_exclusive=min_turn_id_exclusive,
            max_turn_id_exclusive=max_turn_id_exclusive,
        )
        return cast(list[ChatTurnRecord], rows)

    async def delete_up_to(
        self,
        *,
        profile_id: str,
        session_id: str,
        max_turn_id_inclusive: int,
        limit: int | None = None,
    ) -> int:
        return await self.repository.delete_up_to(
            profile_id=profile_id,
            session_id=session_id,
            max_turn_id_inclusive=max_turn_id_inclusive,
            limit=limit,
        )


class JsonlChatTranscriptStore:
    """Append-only JSONL transcript storage sharded by session id."""

    def __init__(self, *, root_dir: Path, session: AsyncSession | None = None) -> None:
        self._root_dir = root_dir
        self._session = session
        self._pending_outbox_ids: set[int] = set()

    async def create_turn(
        self,
        *,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ChatTurnRecord:
        return await self._create_turn(
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            assistant_message=assistant_message,
            turn_id=None,
        )

    async def create_turn_with_id(
        self,
        *,
        turn_id: int,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ChatTurnRecord:
        """Persist one JSONL mirror row using the authoritative DB turn id."""

        return await self._create_turn(
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            assistant_message=assistant_message,
            turn_id=max(1, int(turn_id)),
        )

    async def _create_turn(
        self,
        *,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
        turn_id: int | None,
    ) -> ChatTurnRecord:
        created_at = datetime.now(UTC)
        if self._session is not None:
            if turn_id is None:
                database_next_turn_id = (
                    await ChatTurnRepository(self._session).max_turn_id(
                        profile_id=profile_id,
                        session_id=session_id,
                    )
                ) + 1
                jsonl_next_turn_id = await asyncio.to_thread(
                    self._next_turn_id_floor_sync,
                    session_id,
                )
                turn_id = await allocate_session_jsonl_id(
                    self._session,
                    namespace=_TRANSCRIPT_NAMESPACE,
                    session_id=session_id,
                    next_id_at_least=max(database_next_turn_id, jsonl_next_turn_id),
                )
            else:
                await ensure_session_jsonl_next_id(
                    self._session,
                    namespace=_TRANSCRIPT_NAMESPACE,
                    session_id=session_id,
                    next_id_at_least=turn_id + 1,
                )
            row = JsonlChatTurnRead(
                id=turn_id,
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=assistant_message,
                created_at=created_at,
            )
            mutation: dict[str, object] = {
                "kind": "transcript_turn",
                "turn": _turn_to_json(row),
            }
            outbox_id = await enqueue_session_jsonl_mutation(
                self._session,
                namespace=_TRANSCRIPT_NAMESPACE,
                session_id=session_id,
                mutation=mutation,
            )
            self._pending_outbox_ids.add(outbox_id)

            def _append_after_commit() -> None:
                try:
                    self._apply_mutation_sync(mutation)
                finally:
                    self._pending_outbox_ids.discard(outbox_id)

            add_after_commit_callback(self._session, _append_after_commit)
            return row

        if turn_id is None:
            row = await asyncio.to_thread(
                self._reserve_turn_sync,
                session_id,
                profile_id,
                user_message,
                assistant_message,
                created_at,
            )
        else:
            row = JsonlChatTurnRead(
                id=turn_id,
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=assistant_message,
                created_at=created_at,
            )
        await asyncio.to_thread(self._append_reserved_turn_sync, row)
        return row

    async def count(self, *, profile_id: str, session_id: str) -> int:
        await self._backfill_database_turns(profile_id=profile_id, session_id=session_id)
        await self._drain_outbox(session_id=session_id)
        rows = await asyncio.to_thread(self._read_session_turns_sync, session_id)
        return sum(1 for row in rows if row.profile_id == profile_id)

    async def list_recent(
        self,
        *,
        profile_id: str,
        session_id: str,
        limit: int,
        min_turn_id_exclusive: int = 0,
    ) -> list[ChatTurnRecord]:
        if limit < 1:
            return []
        await self._backfill_database_turns(profile_id=profile_id, session_id=session_id)
        await self._drain_outbox(session_id=session_id)
        rows = await asyncio.to_thread(self._read_session_turns_sync, session_id)
        filtered = [
            row
            for row in rows
            if row.profile_id == profile_id and row.id > min_turn_id_exclusive
        ]
        return cast(list[ChatTurnRecord], filtered[-limit:])

    async def list_range(
        self,
        *,
        profile_id: str,
        session_id: str,
        min_turn_id_exclusive: int = 0,
        max_turn_id_exclusive: int | None = None,
    ) -> list[ChatTurnRecord]:
        await self._backfill_database_turns(profile_id=profile_id, session_id=session_id)
        await self._drain_outbox(session_id=session_id)
        rows = await asyncio.to_thread(self._read_session_turns_sync, session_id)
        filtered = [
            row
            for row in rows
            if row.profile_id == profile_id
            and row.id > min_turn_id_exclusive
            and (max_turn_id_exclusive is None or row.id < max_turn_id_exclusive)
        ]
        return cast(list[ChatTurnRecord], filtered)

    async def delete_up_to(
        self,
        *,
        profile_id: str,
        session_id: str,
        max_turn_id_inclusive: int,
        limit: int | None = None,
    ) -> int:
        if max_turn_id_inclusive < 1:
            return 0
        if self._session is not None:
            await self._backfill_database_turns(profile_id=profile_id, session_id=session_id)
            await self._drain_outbox(session_id=session_id)
            rows = await asyncio.to_thread(self._read_session_turns_sync, session_id)
            candidates = [
                row
                for row in rows
                if row.profile_id == profile_id and row.id <= max_turn_id_inclusive
            ]
            if not candidates:
                return 0
            selected = candidates if limit is None else candidates[: max(1, limit)]
            boundary = selected[-1].id
            record: dict[str, object] = {
                "op": "prune",
                "profile_id": profile_id,
                "session_id": session_id,
                "max_turn_id_inclusive": boundary,
                "created_at": datetime.now(UTC).isoformat(),
            }
            mutation: dict[str, object] = {
                "kind": "transcript_prune",
                "record": record,
            }
            outbox_id = await enqueue_session_jsonl_mutation(
                self._session,
                namespace=_TRANSCRIPT_NAMESPACE,
                session_id=session_id,
                mutation=mutation,
            )
            self._pending_outbox_ids.add(outbox_id)

            def _append_after_commit() -> None:
                try:
                    self._apply_mutation_sync(mutation)
                finally:
                    self._pending_outbox_ids.discard(outbox_id)

            add_after_commit_callback(self._session, _append_after_commit)
            return len(selected)

        return await asyncio.to_thread(
            self._append_prune_sync,
            profile_id,
            session_id,
            max_turn_id_inclusive,
            limit,
            datetime.now(UTC),
        )

    def _reserve_turn_sync(
        self,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
        created_at: datetime,
    ) -> JsonlChatTurnRead:
        paths = self._paths_for_session(session_id)
        paths.event_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_file_lock_sync(paths.lock_path):
            turn_id = next_jsonl_sequence_id(paths.seq_path)
            return JsonlChatTurnRead(
                id=turn_id,
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=assistant_message,
                created_at=created_at,
            )

    def _append_reserved_turn_sync(self, row: JsonlChatTurnRead) -> None:
        paths = self._paths_for_session(row.session_id)
        paths.event_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_file_lock_sync(paths.lock_path):
            if not _transcript_turn_exists(paths.event_path, row):
                _append_transcript_record(paths.event_path, _turn_to_json(row))

    def _append_prune_sync(
        self,
        profile_id: str,
        session_id: str,
        max_turn_id_inclusive: int,
        limit: int | None,
        created_at: datetime,
    ) -> int:
        paths = self._paths_for_session(session_id)
        if not paths.event_path.exists():
            return 0
        with sqlite_file_lock_sync(paths.lock_path):
            rows = [
                row
                for row in _read_transcript_rows(paths.event_path)
                if row.profile_id == profile_id and row.id <= max_turn_id_inclusive
            ]
            if not rows:
                return 0
            selected = rows if limit is None else rows[: max(1, limit)]
            boundary = selected[-1].id
            _append_transcript_record(
                paths.event_path,
                {
                    "op": "prune",
                    "profile_id": profile_id,
                    "session_id": session_id,
                    "max_turn_id_inclusive": boundary,
                    "created_at": created_at.isoformat(),
                },
        )
        return len(selected)

    def _read_session_turns_sync(self, session_id: str) -> list[JsonlChatTurnRead]:
        paths = self._paths_for_session(session_id)
        if not paths.event_path.exists():
            return []
        return list(_read_transcript_rows(paths.event_path))

    def _next_turn_id_floor_sync(self, session_id: str) -> int:
        rows = self._read_session_turns_sync(session_id)
        if not rows:
            return 1
        return max(row.id for row in rows) + 1

    def _apply_mutation_sync(self, mutation: dict[str, object]) -> None:
        kind = str(mutation.get("kind") or "")
        if kind == "transcript_turn":
            turn_raw = mutation.get("turn")
            if not isinstance(turn_raw, dict):
                return
            row = _turn_from_json({str(key): value for key, value in turn_raw.items()})
            if row is not None:
                self._append_reserved_turn_sync(row)
            return
        if kind != "transcript_prune":
            return
        record_raw = mutation.get("record")
        if not isinstance(record_raw, dict):
            return
        session_id = str(record_raw.get("session_id") or "")
        if not session_id:
            return
        paths = self._paths_for_session(session_id)
        paths.event_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_file_lock_sync(paths.lock_path):
            _append_transcript_record(
                paths.event_path,
                {str(key): value for key, value in record_raw.items()},
            )

    async def _drain_outbox(self, *, session_id: str | None = None) -> None:
        if self._session is None:
            return
        if self._pending_outbox_ids:
            return
        while True:
            drained = await drain_session_jsonl_outbox(
                self._session,
                namespace=_TRANSCRIPT_NAMESPACE,
                session_id=session_id,
                apply_mutation=self._apply_mutation_sync,
            )
            if drained <= 0:
                return

    async def _backfill_database_turns(self, *, profile_id: str, session_id: str) -> None:
        if self._session is None:
            return
        rows: list[ChatTurnRecord]
        peer_session_factory = create_peer_session_factory(self._session)
        if peer_session_factory is None:
            rows = cast(
                list[ChatTurnRecord],
                await ChatTurnRepository(self._session).list_range(
                    profile_id=profile_id,
                    session_id=session_id,
                ),
            )
        else:
            async with peer_session_factory() as peer_session:
                rows = cast(
                    list[ChatTurnRecord],
                    await ChatTurnRepository(peer_session).list_range(
                        profile_id=profile_id,
                        session_id=session_id,
                    ),
                )
        if not rows:
            return
        for row in rows:
            await asyncio.to_thread(
                self._append_reserved_turn_sync,
                JsonlChatTurnRead(
                    id=row.id,
                    session_id=row.session_id,
                    profile_id=row.profile_id,
                    user_message=row.user_message,
                    assistant_message=row.assistant_message,
                    created_at=datetime.now(UTC),
                ),
            )
        await ensure_session_jsonl_next_id(
            self._session,
            namespace=_TRANSCRIPT_NAMESPACE,
            session_id=session_id,
            next_id_at_least=max(row.id for row in rows) + 1,
        )

    def _paths_for_session(self, session_id: str) -> JsonlSessionPaths:
        return jsonl_paths_for_session(
            root_dir=self._root_dir,
            namespace=_TRANSCRIPT_NAMESPACE,
            session_id=session_id,
        )


@dataclass(slots=True)
class DualChatTranscriptStore:
    """Write-through JSONL mirror while preserving database reads for rollout."""

    primary: DatabaseChatTranscriptStore
    mirror: JsonlChatTranscriptStore

    async def create_turn(
        self,
        *,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ChatTurnRecord:
        row = await self.primary.create_turn(
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        await self.mirror.create_turn_with_id(
            turn_id=row.id,
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        return row

    async def count(self, *, profile_id: str, session_id: str) -> int:
        return await self.primary.count(profile_id=profile_id, session_id=session_id)

    async def list_recent(
        self,
        *,
        profile_id: str,
        session_id: str,
        limit: int,
        min_turn_id_exclusive: int = 0,
    ) -> list[ChatTurnRecord]:
        return await self.primary.list_recent(
            profile_id=profile_id,
            session_id=session_id,
            limit=limit,
            min_turn_id_exclusive=min_turn_id_exclusive,
        )

    async def list_range(
        self,
        *,
        profile_id: str,
        session_id: str,
        min_turn_id_exclusive: int = 0,
        max_turn_id_exclusive: int | None = None,
    ) -> list[ChatTurnRecord]:
        return await self.primary.list_range(
            profile_id=profile_id,
            session_id=session_id,
            min_turn_id_exclusive=min_turn_id_exclusive,
            max_turn_id_exclusive=max_turn_id_exclusive,
        )

    async def delete_up_to(
        self,
        *,
        profile_id: str,
        session_id: str,
        max_turn_id_inclusive: int,
        limit: int | None = None,
    ) -> int:
        deleted = await self.primary.delete_up_to(
            profile_id=profile_id,
            session_id=session_id,
            max_turn_id_inclusive=max_turn_id_inclusive,
            limit=limit,
        )
        if deleted:
            await self.mirror.delete_up_to(
                profile_id=profile_id,
                session_id=session_id,
                max_turn_id_inclusive=max_turn_id_inclusive,
                limit=deleted,
            )
        return deleted


def build_chat_transcript_store(
    *,
    session: AsyncSession,
    settings: Settings,
) -> ChatTranscriptStore:
    """Build the configured chat transcript store for one runtime session."""

    database_store = DatabaseChatTranscriptStore(ChatTurnRepository(session))
    backend = settings.session_transcript_store_backend
    if backend == "database":
        return database_store
    jsonl_store = JsonlChatTranscriptStore(root_dir=settings.root_dir, session=session)
    if backend == "jsonl":
        return jsonl_store
    return DualChatTranscriptStore(primary=database_store, mirror=jsonl_store)


def _append_transcript_record(event_path: Path, record: dict[str, object]) -> None:
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
        handle.write("\n")


def _turn_to_json(turn: JsonlChatTurnRead) -> dict[str, object]:
    return {
        "op": "turn",
        "id": turn.id,
        "session_id": turn.session_id,
        "profile_id": turn.profile_id,
        "user_message": turn.user_message,
        "assistant_message": turn.assistant_message,
        "created_at": turn.created_at.isoformat(),
    }


def _read_transcript_rows(event_path: Path) -> Iterable[JsonlChatTurnRead]:
    rows_by_id: dict[tuple[str, str, int], JsonlChatTurnRead] = {}
    prune_boundaries: dict[tuple[str, str], int] = {}
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        operation = str(raw.get("op") or "")
        if operation == "turn":
            row = _turn_from_json(raw)
            if row is not None:
                rows_by_id.setdefault((row.profile_id, row.session_id, row.id), row)
        elif operation == "prune":
            try:
                key = (str(raw["profile_id"]), str(raw["session_id"]))
                boundary = int(raw["max_turn_id_inclusive"])
            except (KeyError, TypeError, ValueError):
                continue
            prune_boundaries[key] = max(prune_boundaries.get(key, 0), boundary)
    for row in sorted(rows_by_id.values(), key=lambda item: item.id):
        if row.id > prune_boundaries.get((row.profile_id, row.session_id), 0):
            yield row


def _transcript_turn_exists(event_path: Path, turn: JsonlChatTurnRead) -> bool:
    if not event_path.exists():
        return False
    return any(
        row.id == turn.id
        and row.session_id == turn.session_id
        and row.profile_id == turn.profile_id
        for row in _read_transcript_rows(event_path)
    )


def _turn_from_json(raw: dict[str, object]) -> JsonlChatTurnRead | None:
    try:
        turn_id = int(str(raw["id"]))
        session_id = str(raw["session_id"])
        profile_id = str(raw["profile_id"])
        user_message = str(raw["user_message"])
        assistant_message = str(raw["assistant_message"])
        created_at = datetime.fromisoformat(str(raw["created_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    return JsonlChatTurnRead(
        id=turn_id,
        session_id=session_id,
        profile_id=profile_id,
        user_message=user_message,
        assistant_message=assistant_message,
        created_at=created_at,
    )
