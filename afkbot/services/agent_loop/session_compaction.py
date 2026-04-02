"""Session compaction runtime for trusted session summaries."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.chat_session_compaction_repo import ChatSessionCompactionRepository
from afkbot.repositories.chat_turn_repo import ChatTurnRepository
from afkbot.services.agent_loop.session_compaction_summarizer import SessionCompactionSummarizer
from afkbot.services.llm.contracts import LLMProvider

_COMPACTION_STRATEGY = "deterministic_v1"


@dataclass(frozen=True, slots=True)
class SessionCompactionSnapshot:
    """Trusted persisted summary consumed by history assembly."""

    summary_text: str
    compacted_until_turn_id: int
    source_turn_count: int
    strategy: str


@dataclass(frozen=True, slots=True)
class SessionCompactionResult:
    """Outcome metadata for one compaction refresh attempt."""

    updated: bool
    compacted_until_turn_id: int
    source_turn_count: int
    new_turn_count: int


class SessionCompactionService:
    """Persist and load compact session summaries for older turns."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        enabled: bool,
        trigger_turns: int,
        keep_recent_turns: int,
        history_turns: int,
        max_chars: int,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._enabled = enabled
        self._trigger_turns = max(1, trigger_turns)
        self._keep_recent_turns = min(max(0, keep_recent_turns), max(0, history_turns))
        self._summarizer = SessionCompactionSummarizer(
            max_chars=max_chars,
            llm_provider=llm_provider,
        )
        self._compactions = ChatSessionCompactionRepository(session)
        self._turns = ChatTurnRepository(session)

    async def load_snapshot(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> SessionCompactionSnapshot | None:
        """Return current trusted summary for one session when enabled."""

        if not self._enabled:
            return None
        row = await self._compactions.get(profile_id=profile_id, session_id=session_id)
        if row is None or not row.summary_text.strip() or row.compacted_until_turn_id < 1:
            return None
        return SessionCompactionSnapshot(
            summary_text=row.summary_text,
            compacted_until_turn_id=row.compacted_until_turn_id,
            source_turn_count=row.source_turn_count,
            strategy=row.strategy,
        )

    async def refresh_if_needed(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> SessionCompactionResult:
        """Compact older raw turns into one trusted summary when threshold is crossed."""

        if not self._enabled:
            return SessionCompactionResult(False, 0, 0, 0)
        snapshot = await self.load_snapshot(profile_id=profile_id, session_id=session_id)
        total_turns = await self._turns.count(profile_id=profile_id, session_id=session_id)
        if total_turns < self._trigger_turns or total_turns <= self._keep_recent_turns:
            return self._build_result(snapshot=snapshot, new_turn_count=0, updated=False)

        boundary_turn_id = await self._resolve_boundary_turn_id(
            profile_id=profile_id,
            session_id=session_id,
        )
        if boundary_turn_id is None:
            return self._build_result(snapshot=snapshot, new_turn_count=0, updated=False)

        new_turns = await self._turns.list_range(
            profile_id=profile_id,
            session_id=session_id,
            min_turn_id_exclusive=0 if snapshot is None else snapshot.compacted_until_turn_id,
            max_turn_id_exclusive=None if boundary_turn_id == 0 else boundary_turn_id,
        )
        if not new_turns:
            return self._build_result(snapshot=snapshot, new_turn_count=0, updated=False)

        existing_summary = None if snapshot is None else snapshot.summary_text
        summary_build = await self._summarizer.extend(
            existing_summary=existing_summary,
            new_turns=new_turns,
        )
        row = await self._compactions.upsert(
            profile_id=profile_id,
            session_id=session_id,
            summary_text=summary_build.summary_text,
            compacted_until_turn_id=new_turns[-1].id,
            source_turn_count=(0 if snapshot is None else snapshot.source_turn_count) + len(new_turns),
            strategy=summary_build.strategy or _COMPACTION_STRATEGY,
        )
        return SessionCompactionResult(
            updated=True,
            compacted_until_turn_id=row.compacted_until_turn_id,
            source_turn_count=row.source_turn_count,
            new_turn_count=len(new_turns),
        )

    async def _resolve_boundary_turn_id(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> int | None:
        """Return first turn id that must remain raw in provider history."""

        if self._keep_recent_turns < 1:
            return 0
        recent_rows = await self._turns.list_recent(
            profile_id=profile_id,
            session_id=session_id,
            limit=self._keep_recent_turns,
        )
        if len(recent_rows) < self._keep_recent_turns:
            return None
        return recent_rows[0].id

    @staticmethod
    def _build_result(
        *,
        snapshot: SessionCompactionSnapshot | None,
        new_turn_count: int,
        updated: bool,
    ) -> SessionCompactionResult:
        if snapshot is None:
            return SessionCompactionResult(updated, 0, 0, new_turn_count)
        return SessionCompactionResult(
            updated,
            snapshot.compacted_until_turn_id,
            snapshot.source_turn_count,
            new_turn_count,
        )
