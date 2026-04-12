"""Facade for persisted subagent runtime orchestration."""

from __future__ import annotations

from afkbot.services.policy import PolicyEngine
from afkbot.services.subagents.contracts import (
    SubagentLaunchMode,
    SubagentResultResponse,
    SubagentRunAccepted,
    SubagentWaitResponse,
)
from afkbot.services.subagents.executor import SubagentExecutor
from afkbot.services.subagents.launcher import SubagentLauncher
from afkbot.services.subagents.lifecycle import SubagentLifecycle
from afkbot.services.subagents.loader import SubagentLoader
from afkbot.services.subagents.runner import SubagentRunner
from afkbot.services.subagents.state_transitions import SubagentStateTransitions
from afkbot.services.subagents.task_store import SubagentTaskStore
from afkbot.services.tools.base import ToolContext
from afkbot.settings import Settings


class SubagentService:
    """Persisted async subagent lifecycle manager."""

    def __init__(
        self,
        settings: Settings,
        loader: SubagentLoader | None = None,
        runner: SubagentRunner | None = None,
        policy_engine: PolicyEngine | None = None,
        launch_mode: SubagentLaunchMode = "process",
    ) -> None:
        if runner is not None and launch_mode == "process":
            raise ValueError("custom subagent runner requires launch_mode='inline'")
        self._settings = settings
        self._loader = loader or SubagentLoader(settings)
        self._runner = runner or SubagentRunner(settings)
        self._policy_engine = policy_engine or PolicyEngine(root_dir=settings.root_dir)
        self._task_store = SubagentTaskStore(settings)
        self._transitions = SubagentStateTransitions(self._task_store)
        self._launcher = SubagentLauncher(settings=settings, launch_mode=launch_mode)
        self._executor = SubagentExecutor(
            task_store=self._task_store,
            loader=self._loader,
            runner=self._runner,
            transitions=self._transitions,
        )
        self._lifecycle = SubagentLifecycle(
            settings=settings,
            loader=self._loader,
            policy_engine=self._policy_engine,
            task_store=self._task_store,
            launcher=self._launcher,
            transitions=self._transitions,
            execute_task=self._execute_inline,
        )

    async def run(
        self,
        *,
        ctx: ToolContext,
        prompt: str,
        subagent_name: str | None,
        timeout_sec: int | None,
    ) -> SubagentRunAccepted:
        """Accept one persisted subagent task for later or inline execution."""

        return await self._lifecycle.run(
            ctx=ctx,
            prompt=prompt,
            subagent_name=subagent_name,
            timeout_sec=timeout_sec,
        )

    async def wait(
        self,
        *,
        task_id: str,
        timeout_sec: int | None,
        profile_id: str,
        session_id: str,
    ) -> SubagentWaitResponse:
        """Wait for one persisted task to finish within bounded timeout."""

        return await self._lifecycle.wait(
            task_id=task_id,
            timeout_sec=timeout_sec,
            profile_id=profile_id,
            session_id=session_id,
        )

    async def result(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        """Return current or terminal state for one persisted task."""

        return await self._lifecycle.result(
            task_id=task_id,
            profile_id=profile_id,
            session_id=session_id,
        )

    async def cancel(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        """Cancel one owned persisted task."""

        return await self._lifecycle.cancel(
            task_id=task_id,
            profile_id=profile_id,
            session_id=session_id,
        )

    async def execute_persisted_task(self, *, task_id: str) -> bool:
        """Run one persisted task from inline or worker context."""

        return await self._executor.execute_persisted_task(task_id=task_id)

    async def shutdown(self) -> None:
        """Dispose runtime-owned background tasks and persistence resources."""

        await self._launcher.shutdown()
        await self._task_store.shutdown()

    async def _execute_inline(self, task_id: str) -> bool:
        return await self._executor.execute_persisted_task(task_id=task_id)
