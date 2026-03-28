"""Structured plan rendering for chat CLI flows."""

from __future__ import annotations

import sys

from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot

_PLAN_HEADER = "\033[95mAFK Plan\033[0m"


def render_chat_plan(
    snapshot: ChatPlanSnapshot,
    *,
    include_header: bool = True,
    leading_blank_line: bool = False,
    ansi: bool | None = None,
) -> str:
    """Render one stored plan snapshot into a deterministic CLI block."""

    use_ansi = sys.stdout.isatty() if ansi is None else ansi
    if snapshot.steps:
        lines = [
            f"[{'x' if step.completed else ' '}] {step.text}"
            for step in snapshot.steps
        ]
    else:
        lines = [line.rstrip() for line in snapshot.raw_text.splitlines() if line.strip()]
    body = "\n".join(f"  {line}" for line in lines)
    if include_header:
        body = ((_PLAN_HEADER if use_ansi else "AFK Plan") + "\n" + body)
    if leading_blank_line:
        return "\n" + body
    return body
