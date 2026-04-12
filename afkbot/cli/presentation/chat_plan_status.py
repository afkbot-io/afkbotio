"""Helpers for compact stored-plan status surfaces in chat CLI."""

from __future__ import annotations

from afkbot.cli.presentation.terminal_text import sanitize_terminal_text
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.chat_session.session_state import ChatPlanPhase


def stored_plan_status_for_chat_workspace(
    plan: ChatPlanSnapshot | None,
    *,
    phase: ChatPlanPhase | None = None,
) -> str:
    """Render one short stored-plan status string for workspace surfaces."""

    if plan is None:
        return "none"
    phase_prefix = "" if phase is None else f"{phase} · "
    if plan.step_count > 0:
        return f"{phase_prefix}{plan.step_count} step(s)"
    return f"{phase_prefix}raw text"


def plan_summary_for_chat_workspace(plan: ChatPlanSnapshot | None) -> str:
    """Render one compact summary of the stored plan body."""

    if plan is None:
        return "none"
    if plan.steps:
        preview = ", ".join(sanitize_terminal_text(step.text) for step in plan.steps[:2])
        if plan.step_count > 2:
            preview += ", ..."
        return preview or "none"
    first_line = next((line for line in plan.raw_text.splitlines() if line.strip()), "")
    compact = sanitize_terminal_text(first_line.strip())
    if not compact:
        return "none"
    if len(compact) <= 72:
        return compact
    return compact[:69].rstrip() + "..."
