"""Background daemon for detached Task Flow execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from typing import Protocol

from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.settings import Settings, get_settings

_LOGGER = logging.getLogger(__name__)


class TaskFlowRuntimeExecutor(Protocol):
    """Subset of runtime service methods required by the daemon."""

    async def start(self) -> None:
        """Prepare runtime resources."""
        ...

    async def shutdown(self) -> None:
        """Dispose runtime resources."""
        ...

    async def execute_next_claimable_task(self, *, worker_id: str) -> bool:
        """Execute one claimable task and return whether work was found."""
        ...


class TaskFlowRuntimeDaemon:
    """Long-running worker pool for AI-owned Task Flow tasks."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        service: TaskFlowRuntimeExecutor | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self._settings = resolved_settings
        self._service = service or TaskFlowRuntimeService(settings=resolved_settings)
        self._shutdown_event = asyncio.Event()
        self._shutting_down = False
        self._worker_tasks: list[asyncio.Task[None]] = []

    def begin_shutdown(self) -> None:
        """Switch daemon into shutdown mode and notify workers."""

        if self._shutting_down:
            return
        self._shutting_down = True
        self._shutdown_event.set()

    async def start(self) -> None:
        """Start task workers when not already running."""

        if self._worker_tasks:
            return
        self._shutting_down = False
        self._shutdown_event.clear()
        await self._service.start()
        try:
            self._worker_tasks = [
                asyncio.create_task(
                    self._worker_loop(worker_index=index),
                    name=f"taskflow-runtime-worker-{index}",
                )
                for index in range(self._settings.runtime_worker_count)
            ]
        except Exception:
            await self._service.shutdown()
            self._worker_tasks = []
            raise

    async def stop(self) -> None:
        """Gracefully stop workers and release owned runtime resources."""

        self.begin_shutdown()
        shutdown_timeout = max(self._settings.runtime_shutdown_timeout_sec, 0.0)
        worker_tasks = tuple(self._worker_tasks)
        self._worker_tasks = []
        try:
            if worker_tasks:
                for task in worker_tasks:
                    task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*worker_tasks, return_exceptions=True),
                        timeout=shutdown_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    pending = [task for task in worker_tasks if not task.done()]
                    if pending:
                        for task in pending:
                            task.cancel()
                        cleanup_timeout = max(0.05, shutdown_timeout)
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*pending, return_exceptions=True),
                                timeout=cleanup_timeout,
                            )
                        except asyncio.TimeoutError:
                            pass
                    pending = [task for task in worker_tasks if not task.done()]
                    if pending:
                        raise RuntimeError("Task Flow runtime worker shutdown timed out") from exc
        finally:
            await self._service.shutdown()

    async def _worker_loop(self, *, worker_index: int) -> None:
        worker_id = f"taskflow-runtime:{worker_index}"
        poll_interval = max(self._settings.taskflow_runtime_poll_interval_sec, 0.001)
        while not self._shutting_down:
            try:
                processed = await self._service.execute_next_claimable_task(worker_id=worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    "taskflow_runtime_worker_error worker_id=%s",
                    worker_id,
                )
                processed = False
            if processed or self._shutting_down:
                continue
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
