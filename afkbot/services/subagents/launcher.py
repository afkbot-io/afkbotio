"""Launch helpers for inline and detached subagent execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import os
import signal
import subprocess
from typing import Any

from afkbot.services.subagents.contracts import SubagentLaunchMode
from afkbot.services.subagents.runtime_support import spawn_worker
from afkbot.settings import Settings


class SubagentLauncher:
    """Own inline task handles and detached worker spawning policy."""

    def __init__(self, *, settings: Settings, launch_mode: SubagentLaunchMode) -> None:
        self._settings = settings
        self._launch_mode = launch_mode
        self._inline_tasks: dict[str, asyncio.Task[bool]] = {}
        self._processes: dict[str, subprocess.Popen[Any]] = {}

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
            self._inline_tasks[task_id] = task
            task.add_done_callback(lambda _: self._inline_tasks.pop(task_id, None))
            return
        self._reap_finished_processes()
        process = spawn_worker(task_id=task_id, settings=self._settings)
        self._processes[task_id] = process

    def cancel(self, *, task_id: str) -> None:
        """Best-effort cancellation for launch handles owned by this service."""

        task = self._inline_tasks.pop(task_id, None)
        if task is not None:
            task.cancel()
        self._reap_finished_processes()
        process = self._processes.pop(task_id, None)
        if process is None or process.poll() is not None:
            return
        try:
            killpg = getattr(os, "killpg", None)
            if callable(killpg):
                killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except PermissionError:
            process.terminate()

    def _reap_finished_processes(self) -> None:
        finished_task_ids = [
            task_id for task_id, process in self._processes.items() if process.poll() is not None
        ]
        for task_id in finished_task_ids:
            self._processes.pop(task_id, None)

    async def shutdown(self) -> None:
        """Wait for outstanding inline tasks to settle."""

        inline_tasks = tuple(self._inline_tasks.values())
        for task_id in tuple(self._inline_tasks):
            self.cancel(task_id=task_id)
        for task_id in tuple(self._processes):
            self.cancel(task_id=task_id)
        if inline_tasks:
            await asyncio.gather(*inline_tasks, return_exceptions=True)
