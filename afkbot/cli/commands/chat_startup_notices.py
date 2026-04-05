"""Helpers for rendering synthetic startup assistant notices."""

from __future__ import annotations

from afkbot.cli.presentation.chat_turn_output import render_chat_turn_outcome
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.chat_session.turn_flow import ChatTurnOutcome


def build_startup_assistant_outcome(
    *,
    message: str,
    profile_id: str,
    session_id: str,
) -> ChatTurnOutcome:
    """Build one synthetic assistant outcome for pre-chat notices."""

    return ChatTurnOutcome(
        result=TurnResult(
            run_id=0,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message=message),
        )
    )


def render_startup_assistant_message(
    *,
    message: str,
    profile_id: str,
    session_id: str,
) -> str | None:
    """Render one synthetic assistant outcome for sequential chat startup."""

    return render_chat_turn_outcome(
        build_startup_assistant_outcome(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
        ),
        include_header=True,
        leading_blank_line=True,
    )
