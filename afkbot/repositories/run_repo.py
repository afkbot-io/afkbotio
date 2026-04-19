"""Repository for run entities."""

from __future__ import annotations

from sqlalchemy import Select, select, update
from sqlalchemy.exc import InvalidRequestError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_turn import ChatTurn
from afkbot.models.run import Run


class RunRepository:
    """Persistence operations for run and chat turn models."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, session_id: str, profile_id: str, status: str = "completed") -> Run:
        """Create a run record."""

        run = Run(session_id=session_id, profile_id=profile_id, status=status)
        self._session.add(run)
        await self._session.flush()
        return run

    async def create_chat_turn(
        self,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ChatTurn:
        """Create a chat turn record."""

        turn = ChatTurn(
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        self._session.add(turn)
        await self._session.flush()
        return turn

    async def update_status(self, run_id: int, status: str) -> None:
        """Update run status by primary key."""

        try:
            run = await self._session.get(Run, run_id)
        except (InvalidRequestError, PendingRollbackError):
            await self._session.rollback()
            run = await self._session.get(Run, run_id)
        if run is None:
            return
        run.status = status
        try:
            await self._session.flush()
        except (InvalidRequestError, PendingRollbackError):
            await self._session.rollback()
            run = await self._session.get(Run, run_id)
            if run is None:
                return
            run.status = status
            await self._session.flush()

    async def is_cancel_requested(self, run_id: int) -> bool:
        """Return true when run cancellation has been requested in storage."""

        statement = select(Run.cancel_requested).where(Run.id == run_id)
        value = (await self._session.execute(statement)).scalar_one_or_none()
        return bool(value)

    async def request_cancel(self, *, profile_id: str, session_id: str) -> bool:
        """Mark latest running run for session/profile as cancellation requested."""

        statement: Select[tuple[int]] = (
            select(Run.id)
            .where(
                Run.profile_id == profile_id,
                Run.session_id == session_id,
                Run.status == "running",
            )
            .order_by(Run.id.desc())
            .limit(1)
        )
        run_id = (await self._session.execute(statement)).scalar_one_or_none()
        if run_id is None:
            return False
        result = await self._session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(cancel_requested=True)
        )
        return (result.rowcount or 0) > 0

    async def get_latest_run_id(self, profile_id: str, session_id: str) -> int | None:
        """Return latest run id for profile/session pair."""

        statement = (
            select(Run.id)
            .where(
                Run.profile_id == profile_id,
                Run.session_id == session_id,
            )
            .order_by(Run.id.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def is_run_owned_by_session(
        self,
        *,
        run_id: int,
        profile_id: str,
        session_id: str,
    ) -> bool:
        """Return True when run belongs to provided profile/session pair."""

        statement = (
            select(Run.id)
            .where(
                Run.id == run_id,
                Run.profile_id == profile_id,
                Run.session_id == session_id,
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None
