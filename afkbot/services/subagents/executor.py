"""Worker-side execution for persisted subagent tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.services.subagents.loader import SubagentLoader
from afkbot.services.subagents.runner import (
    SubagentExecutionError,
    SubagentExecutionResult,
    SubagentRunner,
)
from afkbot.services.subagents.state_transitions import SubagentStateTransitions
from afkbot.services.subagents.task_store import SubagentTaskStore


class SubagentExecutor:
    """Execute claimed persisted subagent tasks and persist terminal state."""

    def __init__(
        self,
        *,
        task_store: SubagentTaskStore,
        loader: SubagentLoader,
        runner: SubagentRunner,
        transitions: SubagentStateTransitions,
    ) -> None:
        self._task_store = task_store
        self._loader = loader
        self._runner = runner
        self._transitions = transitions

    async def execute_persisted_task(self, *, task_id: str) -> bool:
        """Execute one persisted task from worker process context."""

        await self._task_store.ensure_schema()
        await self._task_store.prune_expired_tasks()
        state = await self._task_store.claim_task_start(
            task_id=task_id,
            started_at=datetime.now(timezone.utc),
        )
        if state is None:
            return False

        try:
            markdown = await self._loader.load_subagent_markdown(
                name=state.subagent_name,
                profile_id=state.profile_id,
            )
        except FileNotFoundError as exc:
            await self._transitions.fail(
                task_id=task_id,
                error_code="subagent_not_found",
                reason=f"Subagent not found: {exc}",
            )
            return False

        try:
            async def _run_with_session(session: AsyncSession) -> SubagentExecutionResult:
                return await asyncio.wait_for(
                    self._runner.execute(
                        session=session,
                        task_id=task_id,
                        profile_id=state.profile_id,
                        parent_session_id=state.session_id,
                        subagent_name=state.subagent_name,
                        subagent_markdown=markdown,
                        prompt=state.prompt,
                    ),
                    timeout=float(state.timeout_sec),
                )

            execution = await self._task_store.with_session(_run_with_session)
            await self._transitions.complete(
                task_id=task_id,
                child_session_id=execution.child_session_id,
                child_run_id=execution.child_run_id,
                output=execution.output,
            )
            return True
        except SubagentExecutionError as exc:
            await self._transitions.fail(
                task_id=task_id,
                error_code=exc.error_code,
                reason=exc.reason,
            )
            return False
        except TimeoutError:
            await self._transitions.timeout(task_id=task_id, timeout_sec=state.timeout_sec)
            return False
        except asyncio.CancelledError:
            await self._transitions.cancel(task_id=task_id)
            raise
        except Exception as exc:  # pragma: no cover - defensive fallback
            await self._transitions.crash(task_id=task_id, exc=exc)
            return False
