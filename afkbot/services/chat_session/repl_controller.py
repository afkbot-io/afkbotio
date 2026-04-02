"""Transport-agnostic controller for interactive queued chat REPL sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from time import monotonic
from typing import Any, Protocol

from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.chat_session.activity_state import starting_chat_activity
from afkbot.services.chat_session.interrupts import clear_current_task_cancellation
from afkbot.services.chat_session.repl_input import ChatReplInputOutcome
from afkbot.services.chat_session.repl_queue import ChatReplTurnQueue
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnInteractiveOptions, ChatTurnOutcome

RunReplTurnFn = Callable[
    [str, Callable[[ProgressEvent], None], ChatReplSessionState, ChatTurnInteractiveOptions],
    Coroutine[Any, Any, ChatTurnOutcome],
]
RunInterruptibleTurnFn = Callable[
    [Callable[[], Coroutine[Any, Any, ChatTurnOutcome]]],
    Coroutine[Any, Any, ChatTurnOutcome | None],
]
ReadInputFn = Callable[[], Coroutine[Any, Any, str]]
ConsumeInputFn = Callable[[str, ChatReplTurnQueue, bool], ChatReplInputOutcome]
RefreshCatalogFn = Callable[[], Coroutine[Any, Any, None]]
EmitTurnOutputFn = Callable[[ChatTurnOutcome | None], None]
EmitNoticeFn = Callable[[str], None]
StateChangeFn = Callable[[ChatReplSessionState], None]
ProgressSinkFn = Callable[[ProgressEvent], None]
AllowBackgroundInputFn = Callable[[ChatReplSessionState], bool]


class ChatReplUX(Protocol):
    """Minimal UX contract needed by the interactive session controller."""

    def begin_agent_turn(self) -> None: ...

    def on_progress(self, event: ProgressEvent) -> None: ...

    def stop_progress(self) -> None: ...


async def run_queueable_chat_session(
    *,
    ux: ChatReplUX,
    read_input: ReadInputFn,
    run_turn: RunReplTurnFn,
    repl_state: ChatReplSessionState,
    refresh_catalog: RefreshCatalogFn,
    consume_input: ConsumeInputFn,
    progress_sink: ProgressSinkFn,
    run_interruptible_turn: RunInterruptibleTurnFn,
    emit_turn_output: EmitTurnOutputFn,
    emit_notice: EmitNoticeFn,
    on_state_change: StateChangeFn | None = None,
    allow_background_input: AllowBackgroundInputFn | None = None,
) -> None:
    """Run an interactive chat session that accepts queued follow-up input."""

    turn_queue = ChatReplTurnQueue()
    input_task: asyncio.Task[str] | None = asyncio.create_task(read_input())
    current_turn_task: asyncio.Task[ChatTurnOutcome | None] | None = None

    async def _pause_background_input_for_prompt() -> None:
        nonlocal input_task
        if input_task is None or input_task.done():
            return
        input_task.cancel()
        with suppress(asyncio.CancelledError):
            await input_task
        input_task = None

    try:
        setattr(progress_sink, "before_interactive_prompt", _pause_background_input_for_prompt)
    except Exception:
        pass

    def _allow_background_input() -> bool:
        if allow_background_input is None:
            return True
        return allow_background_input(repl_state)

    def _emit_state_change() -> None:
        if on_state_change is not None:
            on_state_change(repl_state)

    try:
        while True:
            if current_turn_task is None:
                next_message = turn_queue.pop_next()
                repl_state.queued_messages = turn_queue.size
                _emit_state_change()
                if next_message is not None:
                    if input_task is not None and not _allow_background_input():
                        input_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await input_task
                        input_task = None
                    repl_state.active_turn = True
                    repl_state.active_turn_started_at = monotonic()
                    repl_state.latest_activity = starting_chat_activity()
                    _emit_state_change()
                    ux.begin_agent_turn()
                    message_to_run = next_message

                    async def _run_next_turn() -> ChatTurnOutcome:
                        return await run_turn(
                            message_to_run,
                            progress_sink,
                            repl_state,
                            ChatTurnInteractiveOptions(interactive_confirm=True),
                        )

                    current_turn_task = asyncio.create_task(
                        run_interruptible_turn(_run_next_turn)
                    )
                elif input_task is None:
                    await refresh_catalog()
                    input_task = asyncio.create_task(read_input())

            waitables = [task for task in (input_task, current_turn_task) if task is not None]
            if not waitables:
                return

            try:
                done, _pending = await asyncio.wait(
                    waitables,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                clear_current_task_cancellation()
                if current_turn_task is None:
                    ux.stop_progress()
                    return
                current_turn_task.cancel()
                try:
                    result = await current_turn_task
                except asyncio.CancelledError:
                    ux.stop_progress()
                    return
                finally:
                    repl_state.active_turn = False
                    repl_state.active_turn_started_at = None
                    ux.stop_progress()
                    current_turn_task = None
                    _emit_state_change()
                emit_turn_output(result)
                if turn_queue.exit_requested:
                    return
                if input_task is None:
                    await refresh_catalog()
                    input_task = asyncio.create_task(read_input())
                continue

            completed_input_task = input_task if input_task in done else None
            if completed_input_task is not None:
                try:
                    raw_message = completed_input_task.result()
                except (EOFError, KeyboardInterrupt):
                    input_task = None
                    if current_turn_task is None:
                        ux.stop_progress()
                        return
                    turn_queue.request_exit()
                    repl_state.queued_messages = 0
                    _emit_state_change()
                    emit_notice("Exit requested after current turn. Pending queue cleared.")
                else:
                    input_task = None
                    input_outcome = consume_input(
                        raw_message,
                        turn_queue,
                        current_turn_task is not None,
                    )
                    if input_outcome.message:
                        emit_notice(input_outcome.message)
                    if input_outcome.notice:
                        emit_notice(input_outcome.notice)
                    if input_outcome.exit_repl:
                        ux.stop_progress()
                        return
                if (
                    not turn_queue.exit_requested
                    and input_task is None
                    and (current_turn_task is None or _allow_background_input())
                ):
                    await refresh_catalog()
                    input_task = asyncio.create_task(read_input())

            completed_turn_task = current_turn_task if current_turn_task in done else None
            if completed_turn_task is not None:
                result = completed_turn_task.result()
                current_turn_task = None
                repl_state.active_turn = False
                repl_state.active_turn_started_at = None
                ux.stop_progress()
                repl_state.queued_messages = turn_queue.size
                _emit_state_change()
                await refresh_catalog()
                emit_turn_output(result)
                if turn_queue.exit_requested:
                    return
    finally:
        repl_state.active_turn = False
        repl_state.active_turn_started_at = None
        repl_state.queued_messages = 0
        _emit_state_change()
        ux.stop_progress()
        if current_turn_task is not None and not current_turn_task.done():
            current_turn_task.cancel()
            with suppress(asyncio.CancelledError):
                await current_turn_task
        if input_task is not None:
            input_task.cancel()
            with suppress(asyncio.CancelledError):
                await input_task
