"""Transport-agnostic chat turn orchestration for optional planning-first flows."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.planning_policy import (
    ChatPlanningMode,
    is_explicit_plan_request,
    should_offer_plan,
)
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot, capture_chat_plan
from afkbot.services.chat_session.turn_planning import (
    build_execution_overrides_from_plan,
    build_plan_only_overrides,
)
from afkbot.services.llm.reasoning import ThinkingLevel

RunTurnWithSecureResolution = Callable[..., Coroutine[Any, Any, TurnResult]]
PlanDecisionFn = Callable[[], bool | Awaitable[bool]]
PlanPresentationFn = Callable[
    [TurnResult, ChatPlanSnapshot | None],
    None | Awaitable[None],
]
PlanRecorderFn = Callable[[ChatPlanSnapshot], None]


@dataclass(frozen=True, slots=True)
class ChatTurnOutcome:
    """CLI-facing turn result plus optional plan-rendering metadata."""

    result: TurnResult
    plan_snapshot: ChatPlanSnapshot | None = None
    final_output: Literal["assistant", "plan", "none"] = "assistant"


@dataclass(frozen=True, slots=True)
class ChatTurnInteractiveOptions:
    """Interactive turn callbacks that may override default REPL confirmations."""

    interactive_confirm: bool
    prompt_to_plan_first: PlanDecisionFn | None = None
    confirm_plan_execution: PlanDecisionFn | None = None
    present_plan: PlanPresentationFn | None = None


async def run_chat_turn_with_optional_planning(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    progress_sink: Callable[[ProgressEvent], None] | None,
    allow_secure_prompt: bool,
    run_turn_with_secure_resolution: RunTurnWithSecureResolution,
    planning_mode: ChatPlanningMode | None,
    thinking_level: ThinkingLevel | None,
    prompt_to_plan_first: PlanDecisionFn | None = None,
    confirm_plan_execution: PlanDecisionFn | None = None,
    present_plan: PlanPresentationFn | None = None,
    record_plan: PlanRecorderFn | None = None,
) -> ChatTurnOutcome:
    """Optionally run one safe planning turn before the execution turn."""

    explicit_plan_request = is_explicit_plan_request(message)
    should_plan_first = explicit_plan_request or planning_mode == "on"
    if (
        not should_plan_first
        and planning_mode == "auto"
        and prompt_to_plan_first is not None
        and should_offer_plan(message=message)
    ):
        should_plan_first = await _resolve_plan_decision(prompt_to_plan_first)

    execution_overrides = _execution_overrides(
        planning_mode=planning_mode,
        thinking_level=thinking_level,
    )
    if not should_plan_first:
        return ChatTurnOutcome(
            result=await run_turn_with_secure_resolution(
                message=message,
                profile_id=profile_id,
                session_id=session_id,
                progress_sink=progress_sink,
                allow_secure_prompt=allow_secure_prompt,
                turn_overrides=execution_overrides,
            )
        )

    plan_result = await run_turn_with_secure_resolution(
        message=message,
        profile_id=profile_id,
        session_id=session_id,
        progress_sink=progress_sink,
        allow_secure_prompt=False,
        turn_overrides=build_plan_only_overrides(
            base_overrides=execution_overrides,
            thinking_level=thinking_level,
        ),
    )
    plan_snapshot = capture_chat_plan(plan_result.envelope.message)
    if plan_snapshot is not None and record_plan is not None:
        record_plan(plan_snapshot)
    if explicit_plan_request or confirm_plan_execution is None:
        return ChatTurnOutcome(
            result=plan_result,
            plan_snapshot=plan_snapshot,
            final_output="plan" if plan_snapshot is not None else "assistant",
        )
    if present_plan is not None:
        await _present_captured_plan(
            present_plan=present_plan,
            plan_result=plan_result,
            plan_snapshot=plan_snapshot,
        )
    if not await _resolve_plan_decision(confirm_plan_execution):
        return ChatTurnOutcome(
            result=plan_result,
            plan_snapshot=plan_snapshot,
            final_output="none",
        )
    return ChatTurnOutcome(
        result=await run_turn_with_secure_resolution(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            progress_sink=progress_sink,
            allow_secure_prompt=allow_secure_prompt,
            turn_overrides=build_execution_overrides_from_plan(
                base_overrides=execution_overrides,
                approved_plan=plan_result.envelope.message,
                thinking_level=thinking_level,
            ),
        ),
        plan_snapshot=plan_snapshot,
    )


def _execution_overrides(
    *,
    planning_mode: ChatPlanningMode | None,
    thinking_level: ThinkingLevel | None,
) -> TurnContextOverrides | None:
    """Build execution overrides for one normal or follow-up turn."""

    if thinking_level is None and planning_mode is None:
        return None
    return TurnContextOverrides(
        thinking_level=thinking_level,
        execution_planning_mode=planning_mode,
    )


async def _resolve_plan_decision(callback: PlanDecisionFn) -> bool:
    """Resolve one plan decision callback that may be sync or async."""

    result = callback()
    if inspect.isawaitable(result):
        return bool(await result)
    return bool(result)


async def _present_captured_plan(
    *,
    present_plan: PlanPresentationFn,
    plan_result: TurnResult,
    plan_snapshot: ChatPlanSnapshot | None,
) -> None:
    """Present one captured plan through a sync or async callback."""

    rendered = present_plan(plan_result, plan_snapshot)
    if inspect.isawaitable(rendered):
        await rendered
