"""Track active turn tasks so they can be cancelled and cleaned up safely."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from afkbot.services.agent_loop.action_contracts import TurnResult


@dataclass(slots=True)
class TrackedTurn:
    """Registry entry for an active turn task."""

    task_id: str
    task: asyncio.Task[TurnResult]
    run_id: int | None = None


class TurnTracker:
    """Own the active-turn registry for one process."""

    def __init__(self) -> None:
        self._active_turns: dict[tuple[str, str], TrackedTurn] = {}
        self._active_turns_lock = asyncio.Lock()

    async def start(
        self,
        *,
        profile_id: str,
        session_id: str,
        task_factory: Callable[[str], asyncio.Task[TurnResult]],
    ) -> tuple[str, asyncio.Task[TurnResult]]:
        """Create and register one tracked turn task."""

        key = (profile_id, session_id)
        task_id = uuid4().hex
        async with self._active_turns_lock:
            current = self._active_turns.get(key)
            if current is not None and not current.task.done():
                raise RuntimeError("turn_already_running")
            task = task_factory(task_id)
            self._active_turns[key] = TrackedTurn(task_id=task_id, task=task)
        task.add_done_callback(
            lambda _: asyncio.create_task(
                self.cleanup_by_task_id(
                    profile_id=profile_id,
                    session_id=session_id,
                    task_id=task_id,
                )
            ),
        )
        return task_id, task

    async def cancel(self, *, profile_id: str, session_id: str) -> bool:
        """Cancel active turn for session and profile, if it exists."""

        key = (profile_id, session_id)
        async with self._active_turns_lock:
            tracked = self._active_turns.get(key)
            if tracked is not None and tracked.task.done():
                self._active_turns.pop(key, None)
                tracked = None
        if tracked is None:
            return False
        tracked.task.cancel()
        return True

    async def set_run_id(self, *, profile_id: str, session_id: str, run_id: int) -> None:
        """Bind the created run id to the current tracked turn task."""

        key = (profile_id, session_id)
        current = asyncio.current_task()
        if current is None:
            return
        async with self._active_turns_lock:
            tracked = self._active_turns.get(key)
            if tracked is not None and tracked.task is current:
                tracked.run_id = run_id

    async def cleanup_by_task_id(
        self,
        *,
        profile_id: str,
        session_id: str,
        task_id: str,
    ) -> None:
        """Drop one tracked turn when its task id still matches the registry."""

        key = (profile_id, session_id)
        async with self._active_turns_lock:
            tracked = self._active_turns.get(key)
            if tracked is not None and tracked.task_id == task_id:
                self._active_turns.pop(key, None)

    async def cleanup_by_task(
        self,
        *,
        profile_id: str,
        session_id: str,
        task: asyncio.Task[object] | None,
    ) -> None:
        """Drop one tracked turn when its task object still matches the registry."""

        if task is None:
            return
        key = (profile_id, session_id)
        async with self._active_turns_lock:
            tracked = self._active_turns.get(key)
            if tracked is not None and tracked.task is task:
                self._active_turns.pop(key, None)
