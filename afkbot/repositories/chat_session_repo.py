"""Repository for chat session entities."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_session import ChatSession


class ChatSessionRepository:
    """Persistence operations for ChatSession model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: str) -> ChatSession | None:
        """Get chat session by id."""

        return await self._session.get(ChatSession, session_id)

    async def create(
        self,
        session_id: str,
        profile_id: str,
        title: str | None = None,
    ) -> ChatSession:
        """Create chat session."""

        resolved_title = str(title or "").strip() or session_id
        row = ChatSession(
            id=session_id,
            profile_id=profile_id,
            title=resolved_title,
            status="active",
        )
        self._session.add(row)
        await self._session.flush()
        return row
