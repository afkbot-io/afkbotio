"""Shared planning-mode policy and heuristics for chat/runtime flows."""

from __future__ import annotations

import re
from typing import Literal

ChatPlanningMode = Literal["off", "auto", "on"]

_EXPLICIT_PLAN_RE = re.compile(
    r"(?:\bplan\b(?![-/])|\bplanning\b(?![-/])|step[- ]by[- ]step|outline|think first|"
    r"состав[^\n]{0,12}план|спланир|пошагов|этап(?:ы|ам|ов)?|продумай)",
    re.IGNORECASE,
)
_COMPLEX_TASK_RE = re.compile(
    r"(?:implement|build|refactor|rewrite|migrate|design|architecture|investigate|analyze|"
    r"debug|fix|integrate|review|optimize|audit|compare|document|update|"
    r"реализ|доработ|проработ|исслед|рефактор|мигрир|исправ|интегр|оптимиз|"
    r"архитект|сравн|документ|обнов|ревью|аудит)",
    re.IGNORECASE,
)
_EXECUTION_PLANNING_OVERLAY = """# Execution Planning
This task requires planning before execution.

Requirements:
- derive a concise internal step-by-step plan before using tools or finalizing;
- sequence tool calls according to that plan and revise the plan when facts change;
- keep track of completed and remaining steps while executing;
- do not output the full plan unless the user explicitly asked for it;
- continue into execution after planning unless another runtime mode forbids execution.
"""


def normalize_chat_planning_mode(value: str | None) -> ChatPlanningMode | None:
    """Normalize runtime/CLI planning mode alias."""

    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        return None
    if normalized not in {"off", "auto", "on"}:
        raise ValueError("plan mode must be one of: off, auto, on")
    return normalized  # type: ignore[return-value]


def should_offer_plan(*, message: str) -> bool:
    """Heuristically detect requests where plan-first UX likely improves quality."""

    text = message.strip()
    if not text:
        return False
    if is_explicit_plan_request(text):
        return False
    if text.count("\n") >= 2:
        return True
    if len(text) >= 220:
        return True
    if _COMPLEX_TASK_RE.search(text) is None:
        return False
    coordination_markers = (
        " and ",
        " then ",
        " after ",
        " before ",
        " а также ",
        " потом ",
        " затем ",
        " после ",
        " сначала ",
        ",",
    )
    lowered = text.lower()
    if any(marker in lowered for marker in coordination_markers):
        return True
    return len(text) >= 96


def is_explicit_plan_request(message: str) -> bool:
    """Return whether the user is directly asking for a plan or outline."""

    return _EXPLICIT_PLAN_RE.search(message.strip()) is not None


def should_enable_execution_planning(
    *,
    message: str,
    planning_mode: ChatPlanningMode,
) -> bool:
    """Return whether runtime should inject internal execution-planning guidance."""

    text = message.strip()
    if not text or planning_mode == "off":
        return False
    if planning_mode == "on":
        return True
    if is_explicit_plan_request(text):
        return False
    return should_offer_plan(message=text)


def execution_planning_prompt_overlay() -> str:
    """Return trusted prompt overlay that asks the model to plan before execution."""

    return _EXECUTION_PLANNING_OVERLAY


__all__ = [
    "ChatPlanningMode",
    "execution_planning_prompt_overlay",
    "is_explicit_plan_request",
    "normalize_chat_planning_mode",
    "should_enable_execution_planning",
    "should_offer_plan",
]
