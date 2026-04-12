"""Tests for the transport-agnostic queued chat session controller."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.chat_session.repl_input import ChatReplInputOutcome
from afkbot.services.chat_session.repl_queue import ChatReplTurnQueue
from afkbot.services.chat_session.repl_controller import run_queueable_chat_session
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnInteractiveOptions, ChatTurnOutcome


@dataclass
class _FakeUX:
    """Minimal UX double for queued session-controller tests."""

    started_turns: int = 0
    stopped_progress: int = 0
    seen_progress_events: list[object] = field(default_factory=list)

    def begin_agent_turn(self) -> None:
        self.started_turns += 1

    def on_progress(self, event: object) -> None:
        self.seen_progress_events.append(event)

    def stop_progress(self) -> None:
        self.stopped_progress += 1


async def _wait_until(predicate, *, timeout_sec: float = 1.0) -> None:
    """Wait until the provided predicate returns true."""

    deadline = asyncio.get_running_loop().time() + timeout_sec
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("timed out waiting for controller condition")
        await asyncio.sleep(0.01)


async def test_run_queueable_chat_session_runs_queued_follow_up_after_current_turn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Queued input should execute after the active turn finishes."""

    # Arrange
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    ux = _FakeUX()
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    seen_messages: list[str] = []
    seen_turn_start_activity: list[str | None] = []
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()
    second_turn_started = asyncio.Event()

    async def _read_input() -> str:
        return await input_queue.get()

    async def _run_turn(
        message: str,
        progress_sink: object,
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        _ = progress_sink, turn_options
        seen_messages.append(message)
        seen_turn_start_activity.append(
            None if repl_state.latest_activity is None else repl_state.latest_activity.summary
        )
        if message == "first":
            first_turn_started.set()
            await release_first_turn.wait()
        if message == "second":
            second_turn_started.set()
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=len(seen_messages),
                session_id="s-queued",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message=f"done:{message}"),
            )
        )

    def _consume_input(
        raw_message: str,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
    ) -> ChatReplInputOutcome:
        if raw_message == "//quit":
            turn_queue.request_exit()
            state.queued_messages = 0
            if turn_active:
                return ChatReplInputOutcome(
                    consumed=True,
                    exit_repl=False,
                    notice="Exit requested after current turn. Pending queue cleared.",
                )
            return ChatReplInputOutcome(consumed=True, exit_repl=True)
        if not raw_message.strip():
            return ChatReplInputOutcome(consumed=True)
        pending_count = turn_queue.enqueue(raw_message)
        state.queued_messages = pending_count
        return ChatReplInputOutcome(
            consumed=True,
            notice=(
                f"Queued next message. Pending queue: {pending_count}" if turn_active else None
            ),
        )

    async def _run_interruptible_turn(
        run_turn: Callable[[], Coroutine[object, object, ChatTurnOutcome]],
    ) -> ChatTurnOutcome | None:
        return await run_turn()

    def _emit_turn_output(result: ChatTurnOutcome | None) -> None:
        if result is not None:
            print(result.result.envelope.message)

    loop_task = asyncio.create_task(
        run_queueable_chat_session(
            ux=ux,
            read_input=_read_input,
            run_turn=_run_turn,
            repl_state=state,
            refresh_catalog=_noop_refresh,
            consume_input=_consume_input,
            progress_sink=lambda event: ux.on_progress(event),
            run_interruptible_turn=_run_interruptible_turn,
            emit_turn_output=_emit_turn_output,
            emit_notice=print,
        )
    )

    # Act
    await input_queue.put("first")
    await first_turn_started.wait()
    await input_queue.put("second")
    await _wait_until(lambda: state.queued_messages == 1)
    release_first_turn.set()
    await second_turn_started.wait()
    await input_queue.put("//quit")
    await loop_task

    # Assert
    captured = capsys.readouterr()
    assert seen_messages == ["first", "second"]
    assert seen_turn_start_activity == ["starting", "starting"]
    assert ux.started_turns == 2
    assert state.active_turn is False
    assert state.queued_messages == 0
    assert "Queued next message. Pending queue: 1" in captured.out
    assert "done:first" in captured.out
    assert "done:second" in captured.out


async def test_run_queueable_chat_session_cancels_active_turn_from_input_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The queued-session controller should honor in-band active-turn cancellation."""

    input_queue: asyncio.Queue[str] = asyncio.Queue()
    ux = _FakeUX()
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    seen_messages: list[str] = []
    turn_started = asyncio.Event()
    turn_cancelled = asyncio.Event()

    async def _read_input() -> str:
        return await input_queue.get()

    async def _run_turn(
        message: str,
        progress_sink: object,
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        _ = progress_sink, repl_state, turn_options
        seen_messages.append(message)
        turn_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            turn_cancelled.set()
            raise
        raise AssertionError("unreachable")

    def _consume_input(
        raw_message: str,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
    ) -> ChatReplInputOutcome:
        if raw_message == "//cancel":
            turn_queue.clear()
            state.queued_messages = 0
            return ChatReplInputOutcome(
                consumed=True,
                notice="Cancelling current turn. Pending queue cleared.",
                cancel_active_turn=True,
            )
        if raw_message == "//quit":
            turn_queue.request_exit()
            state.queued_messages = 0
            return ChatReplInputOutcome(consumed=True, exit_repl=True)
        pending_count = turn_queue.enqueue(raw_message)
        state.queued_messages = pending_count
        return ChatReplInputOutcome(consumed=True)

    async def _run_interruptible_turn(
        run_turn: Callable[[], Coroutine[object, object, ChatTurnOutcome]],
    ) -> ChatTurnOutcome | None:
        try:
            return await run_turn()
        except asyncio.CancelledError:
            return None

    loop_task = asyncio.create_task(
        run_queueable_chat_session(
            ux=ux,
            read_input=_read_input,
            run_turn=_run_turn,
            repl_state=state,
            refresh_catalog=_noop_refresh,
            consume_input=_consume_input,
            progress_sink=lambda event: ux.on_progress(event),
            run_interruptible_turn=_run_interruptible_turn,
            emit_turn_output=lambda _result: None,
            emit_notice=print,
        )
    )

    await input_queue.put("first")
    await turn_started.wait()
    await input_queue.put("//cancel")
    await _wait_until(turn_cancelled.is_set)
    await _wait_until(lambda: state.active_turn is False)
    await input_queue.put("//quit")
    await loop_task

    captured = capsys.readouterr()
    assert seen_messages == ["first"]
    assert turn_cancelled.is_set() is True
    assert state.queued_messages == 0
    assert "Cancelling current turn. Pending queue cleared." in captured.out


async def test_run_queueable_chat_session_pauses_background_input_for_blocking_turns() -> None:
    """Blocking plan-confirmation turns should pause the background prompt reader."""

    # Arrange
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    ux = _FakeUX()
    state = ChatReplSessionState(
        planning_mode="on",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()
    second_read_started = asyncio.Event()
    allow_second_read = asyncio.Event()
    read_calls = 0

    async def _read_input() -> str:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            return await input_queue.get()
        second_read_started.set()
        await allow_second_read.wait()
        return await input_queue.get()

    async def _run_turn(
        message: str,
        progress_sink: object,
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        _ = message, progress_sink, repl_state, turn_options
        first_turn_started.set()
        await release_first_turn.wait()
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=1,
                session_id="s-plan-blocking",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="done"),
            )
        )

    def _consume_input(
        raw_message: str,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
    ) -> ChatReplInputOutcome:
        _ = turn_active
        if raw_message == "//quit":
            turn_queue.request_exit()
            return ChatReplInputOutcome(consumed=True, exit_repl=True)
        pending_count = turn_queue.enqueue(raw_message)
        state.queued_messages = pending_count
        return ChatReplInputOutcome(consumed=True)

    async def _run_interruptible_turn(
        run_turn: Callable[[], Coroutine[object, object, ChatTurnOutcome]],
    ) -> ChatTurnOutcome | None:
        return await run_turn()

    loop_task = asyncio.create_task(
        run_queueable_chat_session(
            ux=ux,
            read_input=_read_input,
            run_turn=_run_turn,
            repl_state=state,
            refresh_catalog=_noop_refresh,
            consume_input=_consume_input,
            progress_sink=lambda event: ux.on_progress(event),
            run_interruptible_turn=_run_interruptible_turn,
            emit_turn_output=lambda _result: None,
            emit_notice=lambda _message: None,
            allow_background_input=lambda _state: False,
        )
    )

    # Act
    await input_queue.put("first")
    await first_turn_started.wait()
    await asyncio.sleep(0.05)
    assert second_read_started.is_set() is False
    release_first_turn.set()
    await _wait_until(second_read_started.is_set)
    await input_queue.put("//quit")
    allow_second_read.set()
    await loop_task

    # Assert
    assert second_read_started.is_set() is True
    assert read_calls == 2


async def test_run_queueable_chat_session_cancels_running_turn_during_final_cleanup() -> None:
    """Controller cleanup should cancel the in-flight turn when another callback fails."""

    # Arrange
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    ux = _FakeUX()
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    turn_started = asyncio.Event()
    turn_cancelled = asyncio.Event()

    async def _read_input() -> str:
        return await input_queue.get()

    async def _run_turn(
        message: str,
        progress_sink: object,
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        _ = message, progress_sink, repl_state, turn_options
        turn_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            turn_cancelled.set()
            raise
        raise AssertionError("unreachable")

    def _consume_input(
        raw_message: str,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
    ) -> ChatReplInputOutcome:
        pending_count = turn_queue.enqueue(raw_message)
        state.queued_messages = pending_count
        return ChatReplInputOutcome(
            consumed=True,
            notice="explode" if turn_active else None,
        )

    async def _run_interruptible_turn(
        run_turn: Callable[[], Coroutine[object, object, ChatTurnOutcome]],
    ) -> ChatTurnOutcome | None:
        return await run_turn()

    async def _run_controller() -> None:
        await run_queueable_chat_session(
            ux=ux,
            read_input=_read_input,
            run_turn=_run_turn,
            repl_state=state,
            refresh_catalog=_noop_refresh,
            consume_input=_consume_input,
            progress_sink=lambda event: ux.on_progress(event),
            run_interruptible_turn=_run_interruptible_turn,
            emit_turn_output=lambda _result: None,
            emit_notice=lambda message: (_ for _ in ()).throw(RuntimeError(message)),
        )

    loop_task = asyncio.create_task(_run_controller())

    # Act
    await input_queue.put("first")
    await turn_started.wait()
    await input_queue.put("second")
    with pytest.raises(RuntimeError, match="explode"):
        await loop_task

    # Assert
    assert turn_cancelled.is_set() is True
    assert state.active_turn is False
    assert state.queued_messages == 0


async def test_run_queueable_chat_session_pauses_background_input_before_interactive_prompt() -> (
    None
):
    """Interactive prompt hook should cancel the active background input reader."""

    input_queue: asyncio.Queue[str] = asyncio.Queue()
    ux = _FakeUX()
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    read_calls = 0
    second_read_started = asyncio.Event()
    second_read_cancelled = asyncio.Event()

    async def _read_input() -> str:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            return await input_queue.get()
        if read_calls >= 3:
            return await input_queue.get()
        second_read_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            second_read_cancelled.set()
            raise
        raise AssertionError("unreachable")

    async def _run_turn(
        message: str,
        progress_sink: object,
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        _ = message, repl_state, turn_options
        await _wait_until(second_read_started.is_set)
        hook = getattr(progress_sink, "before_interactive_prompt", None)
        assert callable(hook)
        await hook()
        await _wait_until(second_read_cancelled.is_set)
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=1,
                session_id="s-pause-input",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="done"),
            )
        )

    def _consume_input(
        raw_message: str,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
    ) -> ChatReplInputOutcome:
        _ = turn_active
        if raw_message == "//quit":
            turn_queue.request_exit()
            return ChatReplInputOutcome(consumed=True, exit_repl=True)
        pending_count = turn_queue.enqueue(raw_message)
        state.queued_messages = pending_count
        return ChatReplInputOutcome(consumed=True)

    async def _run_interruptible_turn(
        run_turn: Callable[[], Coroutine[object, object, ChatTurnOutcome]],
    ) -> ChatTurnOutcome | None:
        return await run_turn()

    loop_task = asyncio.create_task(
        run_queueable_chat_session(
            ux=ux,
            read_input=_read_input,
            run_turn=_run_turn,
            repl_state=state,
            refresh_catalog=_noop_refresh,
            consume_input=_consume_input,
            progress_sink=lambda event: ux.on_progress(event),
            run_interruptible_turn=_run_interruptible_turn,
            emit_turn_output=lambda _result: None,
            emit_notice=lambda _message: None,
        )
    )

    await input_queue.put("first")
    await _wait_until(second_read_cancelled.is_set)
    await input_queue.put("//quit")
    await loop_task

    assert second_read_cancelled.is_set() is True


async def _noop_refresh() -> None:
    """No-op refresh used by the queued session-controller test."""
