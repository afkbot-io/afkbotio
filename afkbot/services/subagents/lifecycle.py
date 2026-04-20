"""Acceptance and read-path helpers for persisted subagent tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from afkbot.services.policy import PolicyEngine
from afkbot.services.subagents.contracts import (
    SubagentResultResponse,
    SubagentRunAccepted,
    SubagentTaskStatus,
    SubagentWaitResponse,
)
from afkbot.services.subagents.launcher import SubagentLauncher
from afkbot.services.subagents.loader import SubagentLoader
from afkbot.services.subagents.runtime_support import resolve_timeout, resolve_wait_timeout
from afkbot.services.subagents.state_transitions import SubagentStateTransitions
from afkbot.services.subagents.task_store import SubagentTaskStore
from afkbot.services.tools.base import ToolContext
from afkbot.settings import Settings

_FINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout"})


class SubagentLifecycle:
    """Accept new tasks and expose wait/result read paths."""

    def __init__(
        self,
        *,
        settings: Settings,
        loader: SubagentLoader,
        policy_engine: PolicyEngine,
        task_store: SubagentTaskStore,
        launcher: SubagentLauncher,
        transitions: SubagentStateTransitions,
        execute_task: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._settings = settings
        self._loader = loader
        self._policy_engine = policy_engine
        self._task_store = task_store
        self._launcher = launcher
        self._transitions = transitions
        self._execute_task = execute_task

    async def run(
        self,
        *,
        ctx: ToolContext,
        prompt: str,
        subagent_name: str | None,
        timeout_sec: int | None,
    ) -> SubagentRunAccepted:
        """Start one persisted subagent task and return running state."""

        if ctx.actor == "subagent":
            raise PermissionError("subagent_recursive_spawn_forbidden")

        await self._task_store.ensure_schema()
        await self._task_store.prune_expired_tasks()
        info = await self._loader.resolve_subagent(name=subagent_name, profile_id=ctx.profile_id)
        resolved_timeout = resolve_timeout(timeout_sec=timeout_sec, settings=self._settings)
        task_id = uuid4().hex
        created_at = datetime.now(timezone.utc)
        policy = await self._task_store.ensure_profile_policy(profile_id=ctx.profile_id)
        self._policy_engine.ensure_subagent_run_allowed(policy=policy)
        await self._task_store.create_task(
            task_id=task_id,
            profile_id=ctx.profile_id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            subagent_name=info.name,
            prompt=prompt,
            timeout_sec=resolved_timeout,
            created_at=created_at,
        )

        try:
            self._launcher.spawn(task_id=task_id, execute_inline=self._execute_task)
        except Exception as exc:
            await self._transitions.fail(
                task_id=task_id,
                error_code="subagent_worker_spawn_failed",
                reason=f"Failed to start worker: {exc.__class__.__name__}: {exc}",
            )
            raise RuntimeError("subagent_worker_spawn_failed") from exc

        return SubagentRunAccepted(
            task_id=task_id,
            status="running",
            subagent_name=info.name,
            timeout_sec=resolved_timeout,
        )

    async def wait(
        self,
        *,
        task_id: str,
        timeout_sec: int | None,
        profile_id: str,
        session_id: str,
    ) -> SubagentWaitResponse:
        """Wait for one persisted task up to bounded timeout."""

        await self._task_store.ensure_schema()
        await self._task_store.prune_expired_tasks()
        wait_timeout = resolve_wait_timeout(timeout_sec=timeout_sec, settings=self._settings)
        deadline = asyncio.get_running_loop().time() + wait_timeout

        while True:
            self._launcher.reap(task_id=task_id)
            state = await self._task_store.load_state(
                task_id=task_id,
                profile_id=profile_id,
                session_id=session_id,
                mark_overdue_timeout=True,
            )
            if state.status in _FINAL_STATUSES:
                return SubagentWaitResponse(
                    task_id=task_id,
                    status=state.status,
                    done=True,
                    child_session_id=state.child_session_id,
                    child_run_id=state.child_run_id,
                )

            now = asyncio.get_running_loop().time()
            if now >= deadline:
                return SubagentWaitResponse(
                    task_id=task_id,
                    status=state.status,
                    done=False,
                    child_session_id=state.child_session_id,
                    child_run_id=state.child_run_id,
                )
            await asyncio.sleep(min(0.2, max(0.0, deadline - now)))

    async def result(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        """Return current or final result for one persisted task."""

        self._launcher.reap(task_id=task_id)
        await self._task_store.ensure_schema()
        await self._task_store.prune_expired_tasks()
        state = await self._task_store.load_state(
            task_id=task_id,
            profile_id=profile_id,
            session_id=session_id,
            mark_overdue_timeout=True,
        )
        if state.status == "running":
            return SubagentResultResponse(
                task_id=task_id,
                status="running",
                child_session_id=state.child_session_id,
                child_run_id=state.child_run_id,
                error_code="subagent_not_finished",
                reason="Subagent task is still running",
            )
        return SubagentResultResponse(
            task_id=task_id,
            status=cast(SubagentTaskStatus, state.status),
            child_session_id=state.child_session_id,
            child_run_id=state.child_run_id,
            output=state.output,
            error_code=state.error_code,
            reason=state.reason,
        )

    async def cancel(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        """Cancel one owned running task and return its terminal state."""

        await self._task_store.ensure_schema()
        await self._task_store.prune_expired_tasks()
        state = await self._task_store.load_state(
            task_id=task_id,
            profile_id=profile_id,
            session_id=session_id,
            mark_overdue_timeout=False,
        )
        if state.status in _FINAL_STATUSES:
            return SubagentResultResponse(
                task_id=task_id,
                status=state.status,
                child_session_id=state.child_session_id,
                child_run_id=state.child_run_id,
                output=state.output,
                error_code=state.error_code,
                reason=state.reason,
            )

        await self._launcher.cancel(task_id=task_id)
        await self._transitions.cancel(task_id=task_id)
        return SubagentResultResponse(
            task_id=task_id,
            status="cancelled",
            error_code="subagent_cancelled",
            reason="Subagent task was cancelled",
        )
