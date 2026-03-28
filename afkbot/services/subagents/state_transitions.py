"""Terminal-state persistence helpers for persisted subagent tasks."""

from __future__ import annotations

from afkbot.services.subagents.task_store import SubagentTaskStore


class SubagentStateTransitions:
    """Persist terminal task states through one shared task store."""

    def __init__(self, task_store: SubagentTaskStore) -> None:
        self._task_store = task_store

    async def complete(
        self,
        *,
        task_id: str,
        child_session_id: str | None,
        child_run_id: int | None,
        output: str | None,
    ) -> None:
        """Persist completed task output and child trace metadata."""

        await self._set_final_state(
            task_id=task_id,
            status="completed",
            child_session_id=child_session_id,
            child_run_id=child_run_id,
            output=output,
            error_code=None,
            reason=None,
        )

    async def fail(
        self,
        *,
        task_id: str,
        error_code: str,
        reason: str,
        child_session_id: str | None = None,
        child_run_id: int | None = None,
        output: str | None = None,
    ) -> None:
        """Persist failed task state with deterministic error payload."""

        await self._set_final_state(
            task_id=task_id,
            status="failed",
            child_session_id=child_session_id,
            child_run_id=child_run_id,
            output=output,
            error_code=error_code,
            reason=reason,
        )

    async def timeout(self, *, task_id: str, timeout_sec: int) -> None:
        """Persist timeout terminal state for one task."""

        await self._set_final_state(
            task_id=task_id,
            status="timeout",
            child_session_id=None,
            child_run_id=None,
            output=None,
            error_code="subagent_timeout",
            reason=f"Subagent timed out after {timeout_sec} seconds",
        )

    async def cancel(self, *, task_id: str) -> None:
        """Persist cancelled terminal state for one task."""

        await self._set_final_state(
            task_id=task_id,
            status="cancelled",
            child_session_id=None,
            child_run_id=None,
            output=None,
            error_code="subagent_cancelled",
            reason="Subagent task was cancelled",
        )

    async def crash(self, *, task_id: str, exc: Exception) -> None:
        """Persist defensive failure when unexpected runtime exception escapes."""

        await self._set_final_state(
            task_id=task_id,
            status="failed",
            child_session_id=None,
            child_run_id=None,
            output=None,
            error_code="subagent_failed",
            reason=f"{exc.__class__.__name__}: {exc}",
        )

    async def _set_final_state(
        self,
        *,
        task_id: str,
        status: str,
        child_session_id: str | None,
        child_run_id: int | None,
        output: str | None,
        error_code: str | None,
        reason: str | None,
    ) -> None:
        await self._task_store.finish_task(
            task_id=task_id,
            status=status,
            child_session_id=child_session_id,
            child_run_id=child_run_id,
            output=output,
            error_code=error_code,
            reason=reason,
        )
