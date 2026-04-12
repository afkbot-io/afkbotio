"""Tests for interactive planning behavior in chat session runtime helpers."""

from __future__ import annotations

from pytest import MonkeyPatch

from afkbot.cli.commands.chat_planning_runtime import (
    confirm_chat_plan_execution,
    render_captured_plan,
)
from afkbot.cli.commands.chat_session_runtime import _run_repl_turn, run_single_turn
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnInteractiveOptions, ChatTurnOutcome


async def test_run_repl_turn_skips_blocking_plan_prompts_in_auto_mode() -> None:
    """Auto REPL mode should keep queued chat non-blocking without disabling plan heuristics."""

    # Arrange
    captured_callbacks: dict[str, object | None] = {}

    async def _fake_turn_flow(**kwargs: object) -> ChatTurnOutcome:
        captured_callbacks["prompt_to_plan_first"] = kwargs["prompt_to_plan_first"]
        captured_callbacks["confirm_plan_execution"] = kwargs["confirm_plan_execution"]
        captured_callbacks["present_plan"] = kwargs["present_plan"]
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=1,
                session_id="s-chat-auto",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="done"),
            )
        )

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_session_runtime.run_chat_turn_with_optional_planning",
        _fake_turn_flow,
    )
    repl_state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    # Act
    try:
        await _run_repl_turn(
            message="implement the feature and update docs",
            profile_id="default",
            session_id="s-chat-auto",
            progress_sink=lambda _event: None,
            run_turn_with_secure_resolution=lambda **_: None,  # type: ignore[arg-type]
            repl_state=repl_state,
            turn_options=ChatTurnInteractiveOptions(interactive_confirm=True),
        )
    finally:
        monkeypatch.undo()

    # Assert
    assert callable(captured_callbacks["prompt_to_plan_first"]) is True
    assert callable(captured_callbacks["confirm_plan_execution"]) is True
    assert captured_callbacks["prompt_to_plan_first"]() is True
    assert captured_callbacks["confirm_plan_execution"]() is True
    assert captured_callbacks["present_plan"] is None


async def test_run_repl_turn_auto_executes_after_presenting_plan_in_plan_on_mode() -> None:
    """Explicit REPL plan-first mode should show the plan and continue without extra input."""

    # Arrange
    captured_callbacks: dict[str, object | None] = {}

    async def _fake_turn_flow(**kwargs: object) -> ChatTurnOutcome:
        captured_callbacks["prompt_to_plan_first"] = kwargs["prompt_to_plan_first"]
        captured_callbacks["confirm_plan_execution"] = kwargs["confirm_plan_execution"]
        captured_callbacks["present_plan"] = kwargs["present_plan"]
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=1,
                session_id="s-chat-plan-on",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="done"),
            )
        )

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_session_runtime.run_chat_turn_with_optional_planning",
        _fake_turn_flow,
    )
    repl_state = ChatReplSessionState(
        planning_mode="on",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    # Act
    try:
        await _run_repl_turn(
            message="implement the feature and update docs",
            profile_id="default",
            session_id="s-chat-plan-on",
            progress_sink=lambda _event: None,
            run_turn_with_secure_resolution=lambda **_: None,  # type: ignore[arg-type]
            repl_state=repl_state,
            turn_options=ChatTurnInteractiveOptions(interactive_confirm=True),
        )
    finally:
        monkeypatch.undo()

    # Assert
    assert captured_callbacks["prompt_to_plan_first"] is None
    assert callable(captured_callbacks["confirm_plan_execution"]) is True
    assert captured_callbacks["confirm_plan_execution"]() is True
    assert callable(captured_callbacks["present_plan"]) is True


def test_render_captured_plan_falls_back_to_result_for_empty_snapshot() -> None:
    """Empty plan snapshots should reuse the assistant renderer instead of a blank plan block."""

    # Arrange
    result = TurnResult(
        run_id=1,
        session_id="s-empty-plan",
        profile_id="default",
        envelope=ActionEnvelope(action="finalize", message=""),
    )
    snapshot = ChatPlanSnapshot(raw_text="", steps=tuple())

    # Act
    rendered = render_captured_plan(
        plan_result=result,
        plan_snapshot=snapshot,
    )

    # Assert
    assert rendered == "  (empty response)"


async def test_confirm_chat_plan_execution_respects_cancel_result() -> None:
    """Cancelled execution confirms should stop instead of accepting the default action."""

    async def _cancel_prompt(**_: object) -> None:
        return None

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_planning_runtime.run_inline_single_select_async",
        _cancel_prompt,
    )

    try:
        confirmed = await confirm_chat_plan_execution()
    finally:
        monkeypatch.undo()

    assert confirmed is False


def test_run_single_turn_auto_executes_after_plan_without_waiting_for_second_prompt() -> None:
    """One-shot chat should not block the serialized lease on a post-plan confirmation prompt."""

    captured_callbacks: dict[str, object | None] = {}
    monkeypatch = MonkeyPatch()

    async def _fake_turn_flow(**kwargs: object) -> ChatTurnOutcome:
        captured_callbacks["prompt_to_plan_first"] = kwargs["prompt_to_plan_first"]
        captured_callbacks["confirm_plan_execution"] = kwargs["confirm_plan_execution"]
        captured_callbacks["present_plan"] = kwargs["present_plan"]
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=1,
                session_id="s-single-turn",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="done"),
            )
        )

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_session_runtime.run_chat_turn_with_optional_planning",
        _fake_turn_flow,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_session_runtime._supports_interactive_confirm",
        lambda: False,
    )

    try:
        run_single_turn(
            message="implement the feature and update docs",
            profile_id="default",
            session_id="s-single-turn",
            json_output=True,
            run_turn_with_secure_resolution=lambda **_: None,  # type: ignore[arg-type]
            planning_mode="on",
            thinking_level=None,
        )
    finally:
        monkeypatch.undo()

    assert captured_callbacks["confirm_plan_execution"] is None
