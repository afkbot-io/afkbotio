"""Tests for interactive workspace runtime helpers."""

from __future__ import annotations

import asyncio

from afkbot.cli.commands.chat_fullscreen_support import (
    build_workspace_turn_options,
    cancel_background_task,
    interrupt_action,
)
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnInteractiveOptions


def test_build_workspace_turn_options_keeps_auto_mode_non_blocking() -> None:
    """Auto planning should preserve the default REPL options without injected overlays."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    turn_options = ChatTurnInteractiveOptions(interactive_confirm=True)

    # Act
    resolved = build_workspace_turn_options(
        state,
        turn_options,
        confirm_plan_execution=lambda: _bool_result(True),
        present_plan=lambda _result, _plan: _none_result(),
    )

    # Assert
    assert resolved is turn_options
    assert resolved.interactive_confirm is True
    assert resolved.prompt_to_plan_first is None
    assert resolved.confirm_plan_execution is None
    assert resolved.present_plan is None


def test_build_workspace_turn_options_injects_confirm_hooks_for_plan_on_mode() -> None:
    """Explicit plan-on mode should inject only the fullscreen execution callbacks."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="on",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    async def _confirm_plan_execution() -> bool:
        return await _bool_result(True)

    async def _present_plan(_result, _plan) -> None:
        _ = _result, _plan
        await _none_result()

    # Act
    resolved = build_workspace_turn_options(
        state,
        ChatTurnInteractiveOptions(interactive_confirm=True),
        confirm_plan_execution=_confirm_plan_execution,
        present_plan=_present_plan,
    )

    # Assert
    assert resolved.interactive_confirm is True
    assert resolved.prompt_to_plan_first is None
    assert resolved.confirm_plan_execution is _confirm_plan_execution
    assert resolved.present_plan is _present_plan


def test_interrupt_action_cancels_active_turn_before_exit() -> None:
    """Active turns should consume the first Ctrl-C as cancellation."""

    action = interrupt_action(
        active_turn=True,
        session_running=True,
    )

    # Assert
    assert action == "cancel_turn"


def test_interrupt_action_exits_when_no_turn_is_running() -> None:
    """Idle sessions should resolve escape/interrupt requests as session exit."""

    action = interrupt_action(
        active_turn=False,
        session_running=True,
    )

    assert action == "exit_session"


async def test_cancel_background_task_cleans_up_pending_task() -> None:
    """Background-task cleanup should cancel and await pending tasks."""

    # Arrange
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _task_body() -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(_task_body())
    await started.wait()

    # Act
    await cancel_background_task(task)

    # Assert
    assert task.cancelled() is True
    assert cancelled.is_set() is True


async def _bool_result(value: bool) -> bool:
    """Return one deterministic async boolean for callback tests."""

    return value


async def _none_result() -> None:
    """Return one deterministic async no-op for callback tests."""
