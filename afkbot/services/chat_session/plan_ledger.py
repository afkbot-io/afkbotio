"""Plan snapshot helpers for interactive chat sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CHECKBOX_RE = re.compile(r"^(?:[-*]\s+)?\[(?P<mark>[ xX])\]\s+(?P<body>.+)$")
_ORDERED_RE = re.compile(r"^(?P<index>\d+)[.)]\s+(?P<body>.+)$")
_BULLET_RE = re.compile(r"^[-*]\s+(?P<body>.+)$")


@dataclass(frozen=True, slots=True)
class ChatPlanStep:
    """One normalized plan step captured from assistant output."""

    text: str
    completed: bool = False


@dataclass(frozen=True, slots=True)
class ChatPlanSnapshot:
    """Latest stored plan for one interactive chat session."""

    raw_text: str
    steps: tuple[ChatPlanStep, ...] = ()

    @property
    def step_count(self) -> int:
        """Return the number of parsed plan steps."""

        return len(self.steps)


def capture_chat_plan(text: str) -> ChatPlanSnapshot | None:
    """Build one plan snapshot from assistant text when non-empty."""

    normalized = text.strip()
    if not normalized:
        return None
    return ChatPlanSnapshot(
        raw_text=normalized,
        steps=_extract_plan_steps(normalized),
    )


def _extract_plan_steps(text: str) -> tuple[ChatPlanStep, ...]:
    steps: list[ChatPlanStep] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        checkbox_match = _CHECKBOX_RE.match(line)
        if checkbox_match is not None:
            steps.append(
                ChatPlanStep(
                    text=checkbox_match.group("body").strip(),
                    completed=checkbox_match.group("mark").strip().lower() == "x",
                )
            )
            continue
        ordered_match = _ORDERED_RE.match(line)
        if ordered_match is not None:
            steps.append(ChatPlanStep(text=ordered_match.group("body").strip()))
            continue
        bullet_match = _BULLET_RE.match(line)
        if bullet_match is not None:
            steps.append(ChatPlanStep(text=bullet_match.group("body").strip()))
    return tuple(step for step in steps if step.text)
