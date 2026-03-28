"""Launch helpers for inline and detached subagent execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from afkbot.services.subagents.contracts import SubagentLaunchMode
from afkbot.services.subagents.runtime_support import spawn_worker
from afkbot.settings import Settings


class SubagentLauncher:
    """Own inline task handles and detached worker spawning policy."""

    def __init__(self, *, settings: Settings, launch_mode: SubagentLaunchMode) -> None:
        self._settings = settings
        self._launch_mode = launch_mode
        self._inline_tasks: set[asyncio.Task[bool]] = set()

    def spawn(
        self,
        *,
        task_id: str,
        execute_inline: Callable[[str], Awaitable[bool]],
    ) -> None:
        """Launch one persisted task in inline or detached worker mode."""

        if self._launch_mode == "inline":
            async def _run_inline() -> bool:
                return await execute_inline(task_id)

            task: asyncio.Task[bool] = asyncio.create_task(
                _run_inline(),
                name=f"subagent:inline:{task_id}",
            )
            self._inline_tasks.add(task)
            task.add_done_callback(self._inline_tasks.discard)
            return
        spawn_worker(task_id=task_id, settings=self._settings)

    async def shutdown(self) -> None:
        """Wait for outstanding inline tasks to settle."""

        if self._inline_tasks:
            await asyncio.gather(*tuple(self._inline_tasks), return_exceptions=True)
