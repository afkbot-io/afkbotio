"""Launch helpers for inline and detached subagent execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
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
        self._process_stop_timeout_sec = max(0.0, float(settings.runtime_shutdown_timeout_sec))

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

    def reap(self, *, task_id: str | None = None) -> None:
        """Drop finished detached workers from the local process-handle map."""

        task_ids = (task_id,) if task_id is not None else tuple(self._processes)
        for current_task_id in task_ids:
            process = self._processes.get(current_task_id)
            if process is None or process.poll() is None:
                continue
            with contextlib.suppress(Exception):
                process.wait(timeout=0)
            self._processes.pop(current_task_id, None)

    async def cancel(self, *, task_id: str) -> None:
        """Best-effort cancellation for launch handles owned by this service."""

        task = self._inline_tasks.pop(task_id, None)
        if task is not None:
            task.cancel()
        self.reap(task_id=task_id)
        process = self._processes.get(task_id)
        if process is None or process.poll() is not None:
            self._processes.pop(task_id, None)
            return
        self._signal_process(process, signal.SIGTERM)
        exited = await self._wait_for_process_exit(
            task_id=task_id,
            process=process,
            timeout_sec=self._process_stop_timeout_sec,
        )
        if exited:
            return
        self._signal_process(process, signal.SIGKILL)
        await self._wait_for_process_exit(
            task_id=task_id,
            process=process,
            timeout_sec=max(0.1, self._process_stop_timeout_sec),
        )

    def _reap_finished_processes(self) -> None:
        self.reap()

    async def shutdown(self) -> None:
        """Wait for outstanding inline tasks to settle."""

        inline_tasks = tuple(self._inline_tasks.values())
        for task_id in tuple(self._inline_tasks):
            await self.cancel(task_id=task_id)
        for task_id in tuple(self._processes):
            await self.cancel(task_id=task_id)
        if inline_tasks:
            await asyncio.gather(*inline_tasks, return_exceptions=True)
        self._reap_finished_processes()

    async def _wait_for_process_exit(
        self,
        *,
        task_id: str,
        process: subprocess.Popen[Any],
        timeout_sec: float,
    ) -> bool:
        if process.poll() is not None:
            self._processes.pop(task_id, None)
            return True
        timeout = max(0.0, float(timeout_sec))
        if timeout <= 0:
            return False
        try:
            await asyncio.to_thread(process.wait, timeout)
        except subprocess.TimeoutExpired:
            return False
        finally:
            if process.poll() is not None:
                self._processes.pop(task_id, None)
        return process.poll() is not None

    @staticmethod
    def _signal_process(process: subprocess.Popen[Any], sig: signal.Signals) -> None:
        try:
            killpg = getattr(os, "killpg", None)
            if callable(killpg):
                killpg(process.pid, sig)
                return
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except PermissionError:
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()
