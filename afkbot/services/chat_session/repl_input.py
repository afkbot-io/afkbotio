"""Typed outcomes for interactive chat REPL input handling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatReplInputOutcome:
    """Describe how one REPL input line affected local session state."""

    consumed: bool
    exit_repl: bool = False
    message: str | None = None
    notice: str | None = None
    queued_message: str | None = None
    cancel_active_turn: bool = False
