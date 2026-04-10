"""Mutable session state shared across interactive chat REPL turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from afkbot.services.agent_loop.planning_policy import ChatPlanningMode
from afkbot.services.chat_session.activity_state import ChatActivitySnapshot
from afkbot.services.chat_session.input_catalog import ChatInputCatalog
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.llm.reasoning import ThinkingLevel

ChatPlanPhase = Literal["planned", "executing", "completed", "cancelled"]


@dataclass(slots=True)
class ChatReplSessionState:
    """Track local chat-session settings that affect future interactive turns."""

    planning_mode: ChatPlanningMode
    thinking_level: ThinkingLevel | None
    default_planning_mode: ChatPlanningMode
    default_thinking_level: ThinkingLevel | None
    queued_messages: int = 0
    active_turn: bool = False
    active_turn_started_at: float | None = None
    latest_plan: ChatPlanSnapshot | None = None
    latest_plan_phase: ChatPlanPhase | None = None
    latest_activity: ChatActivitySnapshot | None = None
    latest_catalog: ChatInputCatalog | None = None
