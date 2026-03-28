"""Bounded retention/GC helpers for raw chat transcript rows after compaction."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.chat_session_compaction_repo import ChatSessionCompactionRepository
from afkbot.repositories.chat_turn_repo import ChatTurnRepository


@dataclass(frozen=True, slots=True)
class SessionRetentionResult:
    """Outcome metadata for one bounded raw transcript GC pass."""

    deleted_turn_count: int
    scanned_session_count: int


class SessionRetentionService:
    """Delete compacted raw turns in bounded batches outside history assembly path."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        prune_raw_turns: bool,
        gc_batch_size: int = 500,
    ) -> None:
        self._prune_raw_turns = prune_raw_turns
        self._gc_batch_size = max(1, gc_batch_size)
        self._compactions = ChatSessionCompactionRepository(session)
        self._turns = ChatTurnRepository(session)

    async def garbage_collect_session(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> SessionRetentionResult:
        """Delete compacted raw turns for one session in bounded batch."""

        if not self._prune_raw_turns:
            return SessionRetentionResult(deleted_turn_count=0, scanned_session_count=0)
        rows = await self._compactions.list_prunable(
            profile_id=profile_id,
            session_id=session_id,
            limit=1,
        )
        if not rows:
            return SessionRetentionResult(deleted_turn_count=0, scanned_session_count=0)
        row = rows[0]
        deleted = await self._turns.delete_up_to(
            profile_id=row.profile_id,
            session_id=row.session_id,
            max_turn_id_inclusive=row.compacted_until_turn_id,
            limit=self._gc_batch_size,
        )
        return SessionRetentionResult(deleted_turn_count=deleted, scanned_session_count=1)

    async def garbage_collect_profile(
        self,
        *,
        profile_id: str,
        session_id: str | None = None,
    ) -> SessionRetentionResult:
        """Delete compacted raw turns across one profile in bounded batch."""

        if not self._prune_raw_turns:
            return SessionRetentionResult(deleted_turn_count=0, scanned_session_count=0)
        rows = await self._compactions.list_prunable(
            profile_id=profile_id,
            session_id=session_id,
            limit=self._gc_batch_size,
        )
        deleted_total = 0
        for row in rows:
            deleted_total += await self._turns.delete_up_to(
                profile_id=row.profile_id,
                session_id=row.session_id,
                max_turn_id_inclusive=row.compacted_until_turn_id,
                limit=max(1, self._gc_batch_size - deleted_total),
            )
            if deleted_total >= self._gc_batch_size:
                break
        return SessionRetentionResult(
            deleted_turn_count=deleted_total,
            scanned_session_count=len(rows),
        )
