"""Persist runlog/progress events and cross-process cancellation checks."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository

_CANCEL_CHECK_MIN_INTERVAL_SEC = 0.05
_PROGRESS_STAGE_ALIASES = {
    "llm_iteration": "thinking",
    "thinking": "thinking",
    "planning": "planning",
    "tool_executing": "tool_call",
    "tool_call": "tool_call",
    "subagent_wait": "subagent_wait",
    "done": "done",
    "cancelled": "cancelled",
}


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
        self._last_progress_key_by_run: dict[int, tuple[str, int]] = {}
        self._last_cancel_check_at_by_run: dict[int, float] = {}

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
        if event_type in {"turn.finalize", "turn.cancel"}:
            self._last_progress_key_by_run.pop(run_id, None)
            self._last_cancel_check_at_by_run.pop(run_id, None)

    async def log_progress(
        self,
        *,
        run_id: int,
        session_id: str,
        stage: str,
        iteration: int,
    ) -> None:
        """Record progress stage for UI pollers."""

        progress_key = (self._canonical_progress_stage(stage), max(0, int(iteration)))
        if self._last_progress_key_by_run.get(run_id) == progress_key:
            return
        await self.log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.progress",
            payload={"stage": stage, "iteration": iteration},
        )
        self._last_progress_key_by_run[run_id] = progress_key

    async def raise_if_cancel_requested(self, *, run_id: int) -> None:
        """Raise cancellation when cross-process cancel request exists in storage."""

        now = time.monotonic()
        last_checked_at = self._last_cancel_check_at_by_run.get(run_id)
        if last_checked_at is not None and now - last_checked_at < _CANCEL_CHECK_MIN_INTERVAL_SEC:
            return
        if self._session.in_transaction():
            await self._session.commit()
        self._last_cancel_check_at_by_run[run_id] = now
        if await self._run_repo.is_cancel_requested(run_id):
            self._last_progress_key_by_run.pop(run_id, None)
            self._last_cancel_check_at_by_run.pop(run_id, None)
            raise asyncio.CancelledError()

    @staticmethod
    def _canonical_progress_stage(stage: str) -> str:
        """Return one canonical stage key used for deduping progress writes."""

        normalized = str(stage).strip().lower()
        return _PROGRESS_STAGE_ALIASES.get(normalized, normalized or "unknown")
