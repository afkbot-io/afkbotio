"""Persist runlog/progress events and cross-process cancellation checks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository


class RunlogRuntime:
    """Wrap runlog writes and cancellation probes behind one small helper."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        run_repo: RunRepository,
        runlog_repo: RunlogRepository,
        sanitize_value: Callable[[object], object],
        to_payload_dict: Callable[[object], dict[str, object]],
    ) -> None:
        self._session = session
        self._run_repo = run_repo
        self._runlog_repo = runlog_repo
        self._sanitize_value = sanitize_value
        self._to_payload_dict = to_payload_dict

    async def log_skill_read_event(
        self,
        *,
        run_id: int,
        session_id: str,
        skill_name: str,
        tool_name: str,
        size: int,
    ) -> None:
        """Persist deterministic `skill.read` event emitted by invocation guards."""

        await self.log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="skill.read",
            payload={
                "skill_name": skill_name,
                "tool_name": tool_name,
                "size": size,
            },
        )

    async def log_event(
        self,
        *,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        """Write one sanitized runlog row and commit it eagerly for polling readers."""

        await self._runlog_repo.create_event(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=self._to_payload_dict(self._sanitize_value(payload)),
        )
        await self._session.commit()

    async def log_progress(
        self,
        *,
        run_id: int,
        session_id: str,
        stage: str,
        iteration: int,
    ) -> None:
        """Record progress stage for UI pollers."""

        await self.log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.progress",
            payload={"stage": stage, "iteration": iteration},
        )

    async def raise_if_cancel_requested(self, *, run_id: int) -> None:
        """Raise cancellation when cross-process cancel request exists in storage."""

        await self._session.commit()
        if await self._run_repo.is_cancel_requested(run_id):
            raise asyncio.CancelledError()
