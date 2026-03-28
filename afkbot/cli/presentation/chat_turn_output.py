"""Render final chat turn outcomes for CLI presentation."""

from __future__ import annotations

from afkbot.cli.presentation.chat_plan_renderer import render_chat_plan
from afkbot.cli.presentation.chat_renderer import render_chat_result
from afkbot.services.chat_session.turn_flow import ChatTurnOutcome


def render_chat_turn_outcome(
    outcome: ChatTurnOutcome,
    *,
    include_header: bool,
    leading_blank_line: bool,
) -> str | None:
    """Render one turn outcome according to its final output mode."""

    if outcome.final_output == "none":
        return None
    if outcome.final_output == "plan" and outcome.plan_snapshot is not None:
        return render_chat_plan(
            outcome.plan_snapshot,
            include_header=include_header,
            leading_blank_line=leading_blank_line,
        )
    return render_chat_result(
        outcome.result,
        include_header=include_header,
        leading_blank_line=leading_blank_line,
    )
