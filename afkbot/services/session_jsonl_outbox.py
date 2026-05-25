"""Transactional outbox helpers for materialized JSONL session stores."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import SESSION_JSONL_PENDING_OUTBOX_INFO_KEY
from afkbot.models.session_store_outbox import SessionStoreOutbox
from afkbot.models.session_store_sequence import SessionStoreSequence

ApplyMutation = Callable[[dict[str, Any]], None]


async def allocate_session_jsonl_id(
    session: AsyncSession,
    *,
    namespace: str,
    session_id: str,
    next_id_at_least: int = 1,
) -> int:
    """Allocate one durable per-session id in the current DB transaction."""

    minimum_next_id = max(1, int(next_id_at_least))
    row = await session.get(SessionStoreSequence, (namespace, session_id))
    if row is None:
        row = SessionStoreSequence(
            namespace=namespace,
            session_id=session_id,
            next_id=minimum_next_id + 1,
        )
        session.add(row)
        await session.flush()
        return minimum_next_id
    value = max(int(row.next_id), minimum_next_id)
    row.next_id = value + 1
    await session.flush()
    return value


async def ensure_session_jsonl_next_id(
    session: AsyncSession,
    *,
    namespace: str,
    session_id: str,
    next_id_at_least: int,
) -> None:
    """Advance one durable per-session id counter without allocating a row id."""

    next_id = max(1, int(next_id_at_least))
    row = await session.get(SessionStoreSequence, (namespace, session_id))
    if row is None:
        session.add(
            SessionStoreSequence(
                namespace=namespace,
                session_id=session_id,
                next_id=next_id,
            )
        )
        await session.flush()
        return
    if int(row.next_id) >= next_id:
        return
    row.next_id = next_id
    await session.flush()


async def enqueue_session_jsonl_mutation(
    session: AsyncSession,
    *,
    namespace: str,
    session_id: str,
    mutation: dict[str, Any],
) -> int:
    """Persist one JSONL materialization intent in the current DB transaction."""

    row = SessionStoreOutbox(
        namespace=namespace,
        session_id=session_id,
        mutation_json=json.dumps(mutation, ensure_ascii=True, sort_keys=True, default=str),
    )
    session.add(row)
    await session.flush()
    outbox_id = int(row.id)
    pending_ids = session.info.setdefault(SESSION_JSONL_PENDING_OUTBOX_INFO_KEY, set())
    if isinstance(pending_ids, set):
        pending_ids.add(outbox_id)
    return outbox_id


async def drain_session_jsonl_outbox(
    session: AsyncSession,
    *,
    namespace: str,
    apply_mutation: ApplyMutation,
    session_id: str | None = None,
    limit: int = 100,
) -> int:
    """Apply committed outbox rows to JSONL files and delete rows that materialized."""

    if limit < 1:
        return 0
    statement = (
        select(SessionStoreOutbox)
        .where(SessionStoreOutbox.namespace == namespace)
        .order_by(SessionStoreOutbox.id.asc())
        .limit(limit)
    )
    if session_id is not None:
        statement = statement.where(SessionStoreOutbox.session_id == session_id)
    pending_ids = session.info.get(SESSION_JSONL_PENDING_OUTBOX_INFO_KEY)
    if isinstance(pending_ids, set) and pending_ids:
        statement = statement.where(SessionStoreOutbox.id.not_in(pending_ids))
    rows = list((await session.execute(statement)).scalars().all())
    if not rows:
        return 0
    applied_ids: list[int] = []
    for row in rows:
        try:
            mutation = json.loads(row.mutation_json)
        except json.JSONDecodeError:
            mutation = {}
        if isinstance(mutation, dict):
            apply_mutation({str(key): value for key, value in mutation.items()})
        applied_ids.append(row.id)
    await session.execute(delete(SessionStoreOutbox).where(SessionStoreOutbox.id.in_(applied_ids)))
    await session.flush()
    return len(applied_ids)
