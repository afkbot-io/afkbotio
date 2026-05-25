"""Append-heavy session event storage backends."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import add_after_commit_callback, create_peer_session_factory
from afkbot.db.sqlite_lock import sqlite_file_lock_sync
from afkbot.repositories.runlog_repo import RunlogEventRead, RunlogRepository
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

SessionEventStoreBackend = Literal["database", "jsonl", "dual"]
_RUNLOG_NAMESPACE = "session_events"
_DATABASE_BACKFILL_BATCH_SIZE = 1000


class RunlogEventStore(Protocol):
    """Storage interface for runlog/progress events."""

    async def create_event(
        self,
        *,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunlogEventRead:
        """Persist one event and return its read model."""

    async def list_run_events_since(
        self,
        *,
        run_id: int,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        """List run events in ascending order after the provided event id."""

    async def list_session_events(
        self,
        *,
        session_id: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        """List most recent events for one chat session in reverse chronological order."""

    async def list_session_events_by_type(
        self,
        *,
        profile_id: str,
        session_id: str,
        event_type: str,
        limit: int = 50,
    ) -> list[RunlogEventRead]:
        """List recent events for one profile/session filtered by event type."""


@dataclass(slots=True)
class DatabaseRunlogEventStore:
    """Runlog storage backed by the relational runtime database."""

    repository: RunlogRepository

    async def create_event(
        self,
        *,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunlogEventRead:
        row = await self.repository.create_event(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        return RunlogEventRead(
            id=row.id,
            run_id=row.run_id,
            session_id=row.session_id,
            event_type=row.event_type,
            payload_json=row.payload_json,
            created_at=row.created_at,
        )

    async def list_run_events_since(
        self,
        *,
        run_id: int,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        return await self.repository.list_run_events_since(
            run_id=run_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def list_session_events(
        self,
        *,
        session_id: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        return await self.repository.list_session_events(
            session_id=session_id,
            event_type=event_type,
            limit=limit,
        )

    async def list_session_events_by_type(
        self,
        *,
        profile_id: str,
        session_id: str,
        event_type: str,
        limit: int = 50,
    ) -> list[RunlogEventRead]:
        return await self.repository.list_session_events_by_type(
            profile_id=profile_id,
            session_id=session_id,
            event_type=event_type,
            limit=limit,
        )


class JsonlRunlogEventStore:
    """Append-only JSONL runlog storage sharded by session id."""

    def __init__(self, *, root_dir: Path, session: AsyncSession | None = None) -> None:
        self._root_dir = root_dir
        self._session = session
        self._pending_outbox_ids: set[int] = set()

    async def create_event(
        self,
        *,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunlogEventRead:
        return await self._create_event(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            event_id=None,
        )

    async def create_event_with_id(
        self,
        *,
        event_id: int,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunlogEventRead:
        """Persist a JSONL mirror event using the authoritative DB event id."""

        return await self._create_event(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            event_id=max(1, int(event_id)),
        )

    async def _create_event(
        self,
        *,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: int | None,
    ) -> RunlogEventRead:
        payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        if self._session is not None:
            if event_id is None:
                database_next_event_id = (
                    await RunlogRepository(self._session).max_session_event_id(
                        session_id=session_id,
                    )
                ) + 1
                jsonl_next_event_id = await asyncio.to_thread(
                    self._next_event_id_floor_sync,
                    session_id,
                )
                event_id = await allocate_session_jsonl_id(
                    self._session,
                    namespace=_RUNLOG_NAMESPACE,
                    session_id=session_id,
                    next_id_at_least=max(database_next_event_id, jsonl_next_event_id),
                )
            else:
                await ensure_session_jsonl_next_id(
                    self._session,
                    namespace=_RUNLOG_NAMESPACE,
                    session_id=session_id,
                    next_id_at_least=event_id + 1,
                )
            row = RunlogEventRead(
                id=event_id,
                run_id=run_id,
                session_id=session_id,
                event_type=event_type,
                payload_json=payload_json,
                created_at=datetime.now(UTC),
            )
            mutation = {"kind": "runlog_event", "event": _event_to_json(row)}
            outbox_id = await enqueue_session_jsonl_mutation(
                self._session,
                namespace=_RUNLOG_NAMESPACE,
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

        if event_id is None:
            row = await asyncio.to_thread(
                self._reserve_event_sync,
                run_id,
                session_id,
                event_type,
                payload_json,
                datetime.now(UTC),
            )
        else:
            row = RunlogEventRead(
                id=event_id,
                run_id=run_id,
                session_id=session_id,
                event_type=event_type,
                payload_json=payload_json,
                created_at=datetime.now(UTC),
            )
        await asyncio.to_thread(self._append_reserved_sync, row)
        return row

    async def list_run_events_since(
        self,
        *,
        run_id: int,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        if limit <= 0:
            return []
        await self._backfill_database_events(run_id=run_id)
        await self._drain_outbox()
        rows = await asyncio.to_thread(self._read_run_events_sync, run_id)
        return [
            row
            for row in rows
            if row.run_id == run_id and row.id > after_event_id
        ][:limit]

    async def list_session_events(
        self,
        *,
        session_id: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        if limit <= 0:
            return []
        await self._backfill_database_session_events(session_id=session_id)
        await self._drain_outbox(session_id=session_id)
        rows = await asyncio.to_thread(self._read_session_events_sync, session_id)
        if event_type is not None:
            rows = [row for row in rows if row.event_type == event_type]
        rows.sort(key=lambda row: row.id, reverse=True)
        return rows[:limit]

    async def list_session_events_by_type(
        self,
        *,
        profile_id: str,
        session_id: str,
        event_type: str,
        limit: int = 50,
    ) -> list[RunlogEventRead]:
        _ = profile_id
        return await self.list_session_events(
            session_id=session_id,
            event_type=event_type,
            limit=limit,
        )

    def _reserve_event_sync(
        self,
        run_id: int,
        session_id: str,
        event_type: str,
        payload_json: str,
        created_at: datetime,
    ) -> RunlogEventRead:
        paths = self._paths_for_session(session_id)
        paths.event_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_file_lock_sync(paths.lock_path):
            event_id = next_jsonl_sequence_id(paths.seq_path)
            return RunlogEventRead(
                id=event_id,
                run_id=run_id,
                session_id=session_id,
                event_type=event_type,
                payload_json=payload_json,
                created_at=created_at,
            )

    def _append_reserved_sync(self, row: RunlogEventRead) -> None:
        paths = self._paths_for_session(row.session_id)
        paths.event_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_file_lock_sync(paths.lock_path):
            if not _runlog_event_exists(paths.event_path, row):
                with paths.event_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(_event_to_json(row), ensure_ascii=True, sort_keys=True)
                    )
                    handle.write("\n")
            _write_run_index(self._run_index_path(row.run_id), session_id=row.session_id)

    def _apply_mutation_sync(self, mutation: dict[str, Any]) -> None:
        if str(mutation.get("kind") or "") != "runlog_event":
            return
        event_raw = mutation.get("event")
        if not isinstance(event_raw, dict):
            return
        row = _event_from_json({str(key): value for key, value in event_raw.items()})
        if row is not None:
            self._append_reserved_sync(row)

    async def _drain_outbox(self, *, session_id: str | None = None) -> None:
        if self._session is None:
            return
        if self._pending_outbox_ids:
            return
        while True:
            drained = await drain_session_jsonl_outbox(
                self._session,
                namespace=_RUNLOG_NAMESPACE,
                session_id=session_id,
                apply_mutation=self._apply_mutation_sync,
            )
            if drained <= 0:
                return

    async def _backfill_database_events(
        self,
        *,
        run_id: int,
        session_id: str | None = None,
    ) -> None:
        if self._session is None:
            return
        after_event_id = 0
        peer_session_factory = create_peer_session_factory(self._session)
        while True:
            if peer_session_factory is None:
                rows = await RunlogRepository(self._session).list_run_events_since(
                    run_id=run_id,
                    after_event_id=after_event_id,
                    limit=_DATABASE_BACKFILL_BATCH_SIZE,
                )
            else:
                async with peer_session_factory() as peer_session:
                    rows = await RunlogRepository(peer_session).list_run_events_since(
                        run_id=run_id,
                        after_event_id=after_event_id,
                        limit=_DATABASE_BACKFILL_BATCH_SIZE,
                    )
            if not rows:
                return
            await self._materialize_database_events(rows, session_id=session_id)
            after_event_id = max(row.id for row in rows)
            if len(rows) < _DATABASE_BACKFILL_BATCH_SIZE:
                return

    async def _backfill_database_session_events(self, *, session_id: str) -> None:
        if self._session is None:
            return
        after_event_id = 0
        peer_session_factory = create_peer_session_factory(self._session)
        while True:
            if peer_session_factory is None:
                rows = await RunlogRepository(self._session).list_session_events_after(
                    session_id=session_id,
                    after_event_id=after_event_id,
                    limit=_DATABASE_BACKFILL_BATCH_SIZE,
                )
            else:
                async with peer_session_factory() as peer_session:
                    rows = await RunlogRepository(peer_session).list_session_events_after(
                        session_id=session_id,
                        after_event_id=after_event_id,
                        limit=_DATABASE_BACKFILL_BATCH_SIZE,
                    )
            if not rows:
                return
            await self._materialize_database_events(rows, session_id=session_id)
            after_event_id = max(row.id for row in rows)
            if len(rows) < _DATABASE_BACKFILL_BATCH_SIZE:
                return

    async def _materialize_database_events(
        self,
        rows: list[RunlogEventRead],
        *,
        session_id: str | None,
    ) -> None:
        if self._session is None or not rows:
            return
        rows_by_session: dict[str, list[RunlogEventRead]] = {}
        for row in sorted(rows, key=lambda item: item.id):
            await asyncio.to_thread(self._append_reserved_sync, row)
            rows_by_session.setdefault(row.session_id, []).append(row)
        for row_session_id, session_rows in rows_by_session.items():
            if session_id is not None and row_session_id != session_id:
                continue
            await ensure_session_jsonl_next_id(
                self._session,
                namespace=_RUNLOG_NAMESPACE,
                session_id=row_session_id,
                next_id_at_least=max(row.id for row in session_rows) + 1,
            )

    def _read_run_events_sync(self, run_id: int) -> list[RunlogEventRead]:
        session_id = _read_run_index(self._run_index_path(run_id))
        if session_id is None:
            return self._read_all_events_sync()
        rows = [
            row
            for row in self._read_session_events_sync(session_id)
            if row.run_id == run_id
        ]
        rows.sort(key=lambda row: row.id)
        return rows

    def _read_session_events_sync(self, session_id: str) -> list[RunlogEventRead]:
        paths = self._paths_for_session(session_id)
        if not paths.event_path.exists():
            return []
        return list(_read_jsonl_events(paths.event_path))

    def _next_event_id_floor_sync(self, session_id: str) -> int:
        rows = self._read_session_events_sync(session_id)
        if not rows:
            return 1
        return max(row.id for row in rows) + 1

    def _read_all_events_sync(self) -> list[RunlogEventRead]:
        events_dir = self._root_dir / "session_events"
        if not events_dir.exists():
            return []
        rows: list[RunlogEventRead] = []
        for event_path in sorted(events_dir.glob("*/*.jsonl")):
            rows.extend(_read_jsonl_events(event_path))
        rows.sort(key=lambda row: (row.run_id, row.id))
        return rows

    def _paths_for_session(self, session_id: str) -> JsonlSessionPaths:
        return jsonl_paths_for_session(
            root_dir=self._root_dir,
            namespace=_RUNLOG_NAMESPACE,
            session_id=session_id,
        )

    def _run_index_path(self, run_id: int) -> Path:
        shard = f"{max(0, int(run_id)) % 256:02x}"
        return self._root_dir / "session_event_run_index" / shard / f"{int(run_id)}.json"


@dataclass(slots=True)
class DualRunlogEventStore:
    """Write-through JSONL mirror while preserving database reads for rollout."""

    primary: DatabaseRunlogEventStore
    mirror: JsonlRunlogEventStore

    async def create_event(
        self,
        *,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunlogEventRead:
        row = await self.primary.create_event(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        await self.mirror.create_event_with_id(
            event_id=row.id,
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        return row

    async def list_run_events_since(
        self,
        *,
        run_id: int,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        return await self.primary.list_run_events_since(
            run_id=run_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def list_session_events(
        self,
        *,
        session_id: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        return await self.primary.list_session_events(
            session_id=session_id,
            event_type=event_type,
            limit=limit,
        )

    async def list_session_events_by_type(
        self,
        *,
        profile_id: str,
        session_id: str,
        event_type: str,
        limit: int = 50,
    ) -> list[RunlogEventRead]:
        return await self.primary.list_session_events_by_type(
            profile_id=profile_id,
            session_id=session_id,
            event_type=event_type,
            limit=limit,
        )


def build_runlog_event_store(
    *,
    session: AsyncSession,
    settings: Settings,
) -> RunlogEventStore:
    """Build the configured runlog event store for one runtime session."""

    database_store = DatabaseRunlogEventStore(RunlogRepository(session))
    backend = settings.session_event_store_backend
    if backend == "database":
        return database_store
    jsonl_store = JsonlRunlogEventStore(root_dir=settings.root_dir, session=session)
    if backend == "jsonl":
        return jsonl_store
    return DualRunlogEventStore(primary=database_store, mirror=jsonl_store)


def _event_to_json(event: RunlogEventRead) -> dict[str, object]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "event_type": event.event_type,
        "payload_json": event.payload_json,
        "created_at": event.created_at.isoformat(),
    }


def _read_jsonl_events(event_path: Path) -> Iterable[RunlogEventRead]:
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        row = _event_from_json(raw)
        if row is not None:
            yield row


def _runlog_event_exists(event_path: Path, event: RunlogEventRead) -> bool:
    if not event_path.exists():
        return False
    return any(row.id == event.id and row.run_id == event.run_id for row in _read_jsonl_events(event_path))


def _event_from_json(raw: dict[str, object]) -> RunlogEventRead | None:
    try:
        event_id = int(str(raw["id"]))
        run_id = int(str(raw["run_id"]))
        session_id = str(raw["session_id"])
        event_type = str(raw["event_type"])
        payload_json = str(raw["payload_json"])
        created_at = datetime.fromisoformat(str(raw["created_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    return RunlogEventRead(
        id=event_id,
        run_id=run_id,
        session_id=session_id,
        event_type=event_type,
        payload_json=payload_json,
        created_at=created_at,
    )


def _write_run_index(index_path: Path, *, session_id: str) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = index_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps({"session_id": session_id}, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(index_path)


def _read_run_index(index_path: Path) -> str | None:
    if not index_path.exists():
        return None
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    session_id = str(raw.get("session_id") or "").strip()
    return session_id or None
