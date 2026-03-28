"""Tests for guarded turn state machine transitions."""

from __future__ import annotations

import pytest

from afkbot.services.agent_loop.state_machine import StateMachine, TurnState


def test_state_machine_happy_path() -> None:
    """Machine should support full think-plan-execute-finalize path."""

    machine = StateMachine()

    assert machine.state == TurnState.IDLE
    assert machine.think() == TurnState.THINKING
    assert machine.plan() == TurnState.PLANNING
    assert machine.execute_tools() == TurnState.TOOL_EXECUTING
    assert machine.think() == TurnState.THINKING
    assert machine.plan() == TurnState.PLANNING
    assert machine.finalize() == TurnState.FINALIZED


def test_state_machine_cancel_from_active_state() -> None:
    """Cancel transition should work from active states only."""

    machine = StateMachine()
    machine.think()
    machine.plan()

    assert machine.cancel() == TurnState.CANCELLED


def test_state_machine_invalid_transition_raises() -> None:
    """Invalid transitions should raise ValueError with deterministic message."""

    machine = StateMachine()

    with pytest.raises(ValueError):
        machine.finalize()

    machine.think()
    with pytest.raises(ValueError):
        machine.execute_tools()
