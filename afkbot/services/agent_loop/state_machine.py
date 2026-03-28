"""Deterministic guarded state machine for one agent turn."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TurnState(StrEnum):
    """Finite states for one turn execution."""

    IDLE = "idle"
    THINKING = "thinking"
    PLANNING = "planning"
    TOOL_EXECUTING = "tool_executing"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class StateMachine:
    """Deterministic state machine implementation."""

    state: TurnState = TurnState.IDLE

    def think(self) -> TurnState:
        """Transition from IDLE/TOOL_EXECUTING to THINKING."""

        self._transition({TurnState.IDLE, TurnState.TOOL_EXECUTING}, TurnState.THINKING)
        return self.state

    def plan(self) -> TurnState:
        """Transition from THINKING to PLANNING."""

        self._transition({TurnState.THINKING}, TurnState.PLANNING)
        return self.state

    def execute_tools(self) -> TurnState:
        """Transition from PLANNING to TOOL_EXECUTING."""

        self._transition({TurnState.PLANNING}, TurnState.TOOL_EXECUTING)
        return self.state

    def finalize(self) -> TurnState:
        """Transition from active states to FINALIZED."""

        self._transition(
            {TurnState.THINKING, TurnState.PLANNING, TurnState.TOOL_EXECUTING},
            TurnState.FINALIZED,
        )
        return self.state

    def cancel(self) -> TurnState:
        """Transition from active states to CANCELLED."""

        self._transition(
            {TurnState.THINKING, TurnState.PLANNING, TurnState.TOOL_EXECUTING},
            TurnState.CANCELLED,
        )
        return self.state

    def _transition(self, allowed_from: set[TurnState], to_state: TurnState) -> None:
        if self.state not in allowed_from:
            from_values = ", ".join(sorted(item.value for item in allowed_from))
            raise ValueError(
                f"Invalid transition: {self.state.value} -> {to_state.value}; "
                f"allowed from: {from_values}"
            )
        self.state = to_state
