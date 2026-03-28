"""LLM chat history builder backed by persisted chat turns."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.chat_turn_repo import ChatTurnRepository
from afkbot.services.agent_loop.session_compaction import SessionCompactionService
from afkbot.services.llm.contracts import LLMMessage

SanitizeText = Callable[[str], str]


class ChatHistoryBuilder:
    """Build sanitized LLM history from persisted chat turns."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        history_turns: int,
        sanitize: SanitizeText,
        session_compaction: SessionCompactionService,
    ) -> None:
        self._history_turns = max(0, history_turns)
        self._sanitize = sanitize
        self._session_compaction = session_compaction
        self._turns = ChatTurnRepository(session)

    async def build(
        self,
        *,
        profile_id: str,
        session_id: str,
        user_message: str,
    ) -> list[LLMMessage]:
        """Build provider history from previous chat turns plus current user message."""

        history: list[LLMMessage] = []
        await self._session_compaction.refresh_if_needed(
            profile_id=profile_id,
            session_id=session_id,
        )
        snapshot = await self._session_compaction.load_snapshot(
            profile_id=profile_id,
            session_id=session_id,
        )
        if snapshot is not None:
            history.append(
                LLMMessage(
                    role="system",
                    content=self._sanitize(
                        "Trusted compact session summary for earlier turns. "
                        "The full raw transcript before this boundary was pruned from history.\n"
                        f"Compacted through turn {snapshot.compacted_until_turn_id}.\n"
                        f"{snapshot.summary_text}"
                    ),
                )
            )
        for row in await self._turns.list_recent(
            profile_id=profile_id,
            session_id=session_id,
            limit=self._history_turns,
            min_turn_id_exclusive=0 if snapshot is None else snapshot.compacted_until_turn_id,
        ):
            history.append(LLMMessage(role="user", content=self._sanitize(row.user_message)))
            history.append(LLMMessage(role="assistant", content=self._sanitize(row.assistant_message)))
        history.append(LLMMessage(role="user", content=user_message))
        return history
