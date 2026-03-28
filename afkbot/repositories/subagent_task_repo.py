"""Repository for persisted subagent task lifecycle."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.subagent_task import SubagentTask


class SubagentTaskRepository:
    """Persistence operations for cross-process subagent tasks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_task(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
        run_id: int | None,
        subagent_name: str,
        prompt: str,
        timeout_sec: int,
        created_at: datetime,
    ) -> SubagentTask:
        """Create one running task row."""

        row = SubagentTask(
            task_id=task_id,
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            subagent_name=subagent_name,
            prompt=prompt,
            timeout_sec=timeout_sec,
            status="running",
            created_at=created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_task(self, *, task_id: str) -> SubagentTask | None:
        """Get one task row by task id."""

        return await self._session.get(SubagentTask, task_id)

    async def claim_task_start(self, *, task_id: str, started_at: datetime) -> SubagentTask | None:
        """Mark task as started exactly once and return claimed row."""

        statement = (
            update(SubagentTask)
            .where(
                SubagentTask.task_id == task_id,
                SubagentTask.status == "running",
                SubagentTask.started_at.is_(None),
                SubagentTask.finished_at.is_(None),
            )
            .values(started_at=started_at)
            .execution_options(synchronize_session="fetch")
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        rowcount = int(getattr(result, "rowcount", 0) or 0)
        if rowcount <= 0:
            return None
        return await self.get_task(task_id=task_id)

    async def finish_task(
        self,
        *,
        task_id: str,
        status: str,
        finished_at: datetime,
        child_session_id: str | None = None,
        child_run_id: int | None = None,
        output: str | None = None,
        error_code: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Transition one running task into a final state."""

        statement = (
            update(SubagentTask)
            .where(
                SubagentTask.task_id == task_id,
                SubagentTask.status == "running",
                SubagentTask.finished_at.is_(None),
            )
            .values(
                status=status,
                finished_at=finished_at,
                child_session_id=child_session_id,
                child_run_id=child_run_id,
                output=output,
                error_code=error_code,
                reason=reason,
            )
            .execution_options(synchronize_session="fetch")
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        rowcount = int(getattr(result, "rowcount", 0) or 0)
        return rowcount > 0

    async def delete_finished_before(self, *, before_utc: datetime) -> int:
        """Delete finished task rows older than one threshold."""

        statement = delete(SubagentTask).where(
            SubagentTask.finished_at.is_not(None),
            SubagentTask.finished_at < before_utc,
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)
