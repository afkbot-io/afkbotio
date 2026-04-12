"""Contracts for session-level turn orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.tools.base import ToolCall

SessionTurnSource = Literal["chat", "api", "automation", "taskflow", "subagent"]


class SessionTurnRunner(Protocol):
    """Minimal executable turn contract exposed to runtime entrypoints."""

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
        progress_sink: Callable[[ProgressEvent], None] | None = None,
        client_msg_id: str | None = None,
        source: SessionTurnSource = "chat",
    ) -> TurnResult: ...


class SerializedSessionTurnRunner(Protocol):
    """Bound session runner used inside one already-acquired serialized lease."""

    async def run_turn(
        self,
        *,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
        progress_sink: Callable[[ProgressEvent], None] | None = None,
    ) -> TurnResult: ...
