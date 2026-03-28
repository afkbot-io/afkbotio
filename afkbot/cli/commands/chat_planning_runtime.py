"""Shared planning copy and rendering helpers for chat CLI runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from afkbot.cli.commands.chat_planning import ChatPlanningMode
from afkbot.cli.presentation.chat_plan_renderer import render_chat_plan
from afkbot.cli.presentation.chat_renderer import render_chat_result
from afkbot.cli.presentation.inline_select import confirm_space
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.chat_session.turn_flow import PlanDecisionFn, PlanPresentationFn


@dataclass(frozen=True, slots=True)
class ChatPlanningPrompt:
    """Reusable copy bundle for interactive planning choices."""

    title: str
    question: str
    default: bool
    yes_label: str
    no_label: str
    hint_text: str | None = None
    cancel_result: bool | None = None


@dataclass(frozen=True, slots=True)
class ChatReplPlanningCallbacks:
    """Resolved planning callbacks for one interactive REPL turn."""

    prompt_to_plan_first: PlanDecisionFn | None = None
    confirm_plan_execution: PlanDecisionFn | None = None
    present_plan: PlanPresentationFn | None = None


PLAN_FIRST_PROMPT = ChatPlanningPrompt(
    title="Planning",
    question="This looks like a multi-step task. Create a plan first?",
    default=True,
    yes_label="Plan first",
    no_label="Run now",
    hint_text="Plan-first mode uses a safe read-only pass before execution.",
)

PLAN_EXECUTION_PROMPT = ChatPlanningPrompt(
    title="Execution",
    question="Execute the task using this plan?",
    default=True,
    yes_label="Execute",
    no_label="Stop",
    hint_text="The approved plan will be attached to the next execution turn.",
    cancel_result=False,
)


def confirm_chat_plan_first() -> bool:
    """Ask whether one multi-step turn should begin with planning."""

    return _confirm_prompt(PLAN_FIRST_PROMPT)


def confirm_chat_plan_execution() -> bool:
    """Ask whether one captured plan should continue into execution."""

    return _confirm_prompt(PLAN_EXECUTION_PROMPT)


def render_captured_plan(
    *,
    plan_result: TurnResult,
    plan_snapshot: ChatPlanSnapshot | None,
) -> str:
    """Render one captured plan preview for interactive confirmation flows."""

    if plan_snapshot is not None and plan_snapshot_has_visible_content(plan_snapshot):
        rendered_plan: str = render_chat_plan(
            plan_snapshot,
            include_header=True,
            leading_blank_line=False,
        )
        return rendered_plan
    rendered_result: str = render_chat_result(
        plan_result,
        include_header=False,
        leading_blank_line=False,
    )
    return rendered_result


def plan_snapshot_has_visible_content(snapshot: ChatPlanSnapshot) -> bool:
    """Return whether one captured snapshot contains user-visible plan content."""

    return bool(snapshot.steps or snapshot.raw_text.strip())


def build_repl_planning_callbacks(
    *,
    planning_mode: ChatPlanningMode | None,
    interactive_confirm: bool,
    print_intermediate: Callable[[str], None],
) -> ChatReplPlanningCallbacks:
    """Resolve the planning callbacks for one REPL turn."""

    prompt_to_plan_first: PlanDecisionFn | None = None
    confirm_plan_execution: PlanDecisionFn | None = None
    present_plan: PlanPresentationFn | None = None

    if planning_mode == "auto":
        prompt_to_plan_first = accept_plan_automatically
        confirm_plan_execution = accept_plan_automatically
    elif planning_mode == "on":
        if interactive_confirm:
            confirm_plan_execution = confirm_chat_plan_execution

        def _present_plan(plan_result: TurnResult, plan_snapshot: ChatPlanSnapshot | None) -> None:
            print_intermediate(
                render_captured_plan(
                    plan_result=plan_result,
                    plan_snapshot=plan_snapshot,
                )
            )

        present_plan = _present_plan

    return ChatReplPlanningCallbacks(
        prompt_to_plan_first=prompt_to_plan_first,
        confirm_plan_execution=confirm_plan_execution,
        present_plan=present_plan,
    )


def accept_plan_automatically() -> bool:
    """Accept one planning choice without blocking the interactive transport."""

    return True


def _confirm_prompt(prompt: ChatPlanningPrompt) -> bool:
    """Render one inline confirm prompt from shared copy."""

    return confirm_space(
        question=prompt.question,
        default=prompt.default,
        title=prompt.title,
        yes_label=prompt.yes_label,
        no_label=prompt.no_label,
        hint_text=prompt.hint_text,
    )
