"""Cancellation helpers for interactive chat session turns."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

_TurnResultT = TypeVar("_TurnResultT")


async def run_turn_interruptibly(
    *,
    task_name: str,
    run_turn: Callable[[], Coroutine[Any, Any, _TurnResultT]],
    on_interrupt: Callable[[], None],
) -> _TurnResultT | None:
    """Treat the first task cancellation as turn abort and reserve the second for REPL exit."""

    turn_task = asyncio.create_task(run_turn(), name=task_name)
    try:
        return await asyncio.shield(turn_task)
    except asyncio.CancelledError:
        clear_current_task_cancellation()
        on_interrupt()
        turn_task.cancel()
        try:
            return await asyncio.shield(turn_task)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling() > 0:
                raise
            return None


def clear_current_task_cancellation() -> None:
    """Consume one pending cancellation so cleanup awaits can finish before returning to REPL."""

    current_task = asyncio.current_task()
    if current_task is None:
        return
    uncancel = getattr(current_task, "uncancel", None)
    if callable(uncancel):
        uncancel()
