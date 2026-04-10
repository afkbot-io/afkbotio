"""Tests for transport-agnostic chat turn orchestration."""

from __future__ import annotations

import asyncio

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.chat_session.turn_flow import run_chat_turn_with_optional_planning


async def test_run_chat_turn_with_optional_planning_keeps_default_execution_when_mode_missing() -> None:
    """Turn orchestration should not invent planning overrides when callers omit them."""

    # Arrange
    seen_overrides: TurnContextOverrides | None = None

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
    ):
        nonlocal seen_overrides
        _ = progress_sink, allow_secure_prompt
        assert message == "hello"
        assert profile_id == "default"
        assert session_id == "s-turn-flow-default"
        seen_overrides = turn_overrides
        return TurnResult(
            run_id=1,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    # Act
    outcome = await run_chat_turn_with_optional_planning(
        message="hello",
        profile_id="default",
        session_id="s-turn-flow-default",
        progress_sink=None,
        allow_secure_prompt=False,
        run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
        planning_mode=None,
        thinking_level=None,
    )

    # Assert
    assert outcome.result.envelope.message == "done"
    assert outcome.final_output == "assistant"
    assert seen_overrides is None


async def test_run_chat_turn_with_optional_planning_presents_and_records_plan_before_stop() -> None:
    """Plan-first orchestration should capture one plan snapshot before returning a stop outcome."""

    # Arrange
    recorded_plan_steps: list[int] = []
    presented_blocks: list[str] = []
    seen_calls: list[TurnContextOverrides | None] = []

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
    ):
        _ = progress_sink
        assert message == "Implement the feature."
        assert profile_id == "default"
        assert session_id == "s-turn-flow-plan"
        seen_calls.append(turn_overrides)
        if allow_secure_prompt is False:
            return TurnResult(
                run_id=11,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(
                    action="finalize",
                    message="1. Inspect\n2. Implement\n3. Verify",
                ),
            )
        return TurnResult(
            run_id=12,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    # Act
    outcome = await run_chat_turn_with_optional_planning(
        message="Implement the feature.",
        profile_id="default",
        session_id="s-turn-flow-plan",
        progress_sink=None,
        allow_secure_prompt=True,
        run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
        planning_mode="on",
        thinking_level=None,
        prompt_to_plan_first=None,
        confirm_plan_execution=lambda: False,
        present_plan=lambda plan_result, plan_snapshot: presented_blocks.append(
            plan_result.envelope.message if plan_snapshot is None else str(plan_snapshot.step_count)
        ),
        record_plan=lambda snapshot: recorded_plan_steps.append(snapshot.step_count),
    )

    # Assert
    assert outcome.result.envelope.message == "1. Inspect\n2. Implement\n3. Verify"
    assert outcome.final_output == "none"
    assert outcome.plan_snapshot is not None
    assert outcome.plan_snapshot.step_count == 3
    assert len(seen_calls) == 1
    assert recorded_plan_steps == [3]
    assert presented_blocks == ["3"]


async def test_run_chat_turn_with_optional_planning_persists_explicit_plan_requests() -> None:
    """Explicit plan requests should remain durable instead of using ephemeral plan persistence."""

    seen_overrides: list[TurnContextOverrides | None] = []

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
    ):
        _ = progress_sink, allow_secure_prompt
        assert message == "Plan the migration in steps."
        assert profile_id == "default"
        assert session_id == "s-turn-flow-explicit-plan"
        seen_overrides.append(turn_overrides)
        return TurnResult(
            run_id=15,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(
                action="finalize",
                message="1. Inspect\n2. Migrate\n3. Verify",
            ),
        )

    outcome = await run_chat_turn_with_optional_planning(
        message="Plan the migration in steps.",
        profile_id="default",
        session_id="s-turn-flow-explicit-plan",
        progress_sink=None,
        allow_secure_prompt=False,
        run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
        planning_mode="on",
        thinking_level=None,
    )

    assert outcome.final_output == "plan"
    assert outcome.plan_snapshot is not None
    assert len(seen_overrides) == 1
    assert seen_overrides[0] is not None
    assert seen_overrides[0].planning_mode == "plan_only"
    assert seen_overrides[0].persist_turn is True


async def test_run_chat_turn_with_optional_planning_auto_executes_without_confirmation_callback() -> None:
    """Missing plan execution confirmation should continue straight to execution when not explicitly requested."""

    # Arrange
    seen_calls: list[tuple[bool, TurnContextOverrides | None]] = []
    seen_plan_phases: list[str] = []

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
    ):
        _ = progress_sink
        assert message == "Implement the feature."
        assert profile_id == "default"
        assert session_id == "s-turn-flow-auto-exec"
        seen_calls.append((allow_secure_prompt, turn_overrides))
        if allow_secure_prompt is False:
            return TurnResult(
                run_id=31,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(
                    action="finalize",
                    message="1. Inspect\n2. Implement\n3. Verify",
                ),
            )
        return TurnResult(
            run_id=32,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    # Act
    outcome = await run_chat_turn_with_optional_planning(
        message="Implement the feature.",
        profile_id="default",
        session_id="s-turn-flow-auto-exec",
        progress_sink=None,
        allow_secure_prompt=True,
        run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
        planning_mode="on",
        thinking_level=None,
        prompt_to_plan_first=None,
        confirm_plan_execution=None,
        present_plan=None,
        record_plan=None,
        update_plan_phase=seen_plan_phases.append,
    )

    # Assert
    assert outcome.result.envelope.message == "done"
    assert outcome.final_output == "assistant"
    assert len(seen_calls) == 2
    assert seen_calls[0][0] is False
    assert seen_calls[1][0] is True
    assert seen_plan_phases == ["planned", "executing", "completed"]


async def test_run_chat_turn_with_optional_planning_marks_cancelled_plan_execution() -> None:
    """Cancellation during plan-backed execution should mark the stored plan as cancelled."""

    seen_plan_phases: list[str] = []

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
    ):
        _ = message, profile_id, session_id, progress_sink, turn_overrides
        if allow_secure_prompt is False:
            return TurnResult(
                run_id=41,
                session_id="s-turn-flow-cancel",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="1. Inspect\n2. Implement"),
            )
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_chat_turn_with_optional_planning(
            message="Implement the feature.",
            profile_id="default",
            session_id="s-turn-flow-cancel",
            progress_sink=None,
            allow_secure_prompt=True,
            run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
            planning_mode="on",
            thinking_level=None,
            confirm_plan_execution=None,
            update_plan_phase=seen_plan_phases.append,
        )

    assert seen_plan_phases == ["planned", "executing", "cancelled"]


async def test_run_chat_turn_with_optional_planning_auto_mode_can_plan_then_execute_without_prompts() -> None:
    """Auto mode should still run a plan-only pass when the heuristic says to plan first."""

    # Arrange
    seen_calls: list[tuple[bool, TurnContextOverrides | None]] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "afkbot.services.chat_session.turn_flow.should_offer_plan",
        lambda *, message: True,
    )

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
    ):
        _ = progress_sink
        assert message == "Implement the feature."
        assert profile_id == "default"
        assert session_id == "s-turn-flow-auto"
        seen_calls.append((allow_secure_prompt, turn_overrides))
        if allow_secure_prompt is False:
            return TurnResult(
                run_id=21,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(
                    action="finalize",
                    message="1. Inspect\n2. Implement\n3. Verify",
                ),
            )
        return TurnResult(
            run_id=22,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    # Act
    try:
        outcome = await run_chat_turn_with_optional_planning(
            message="Implement the feature.",
            profile_id="default",
            session_id="s-turn-flow-auto",
            progress_sink=None,
            allow_secure_prompt=True,
            run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
            planning_mode="auto",
            thinking_level=None,
            prompt_to_plan_first=lambda: True,
            confirm_plan_execution=lambda: True,
            present_plan=None,
            record_plan=None,
        )
    finally:
        monkeypatch.undo()

    # Assert
    assert outcome.result.envelope.message == "done"
    assert outcome.final_output == "assistant"
    assert outcome.plan_snapshot is not None
    assert outcome.plan_snapshot.step_count == 3
    assert len(seen_calls) == 2
    assert seen_calls[0][0] is False
    assert seen_calls[1][0] is True


async def test_run_chat_turn_with_optional_planning_forwards_secure_prompt_callbacks() -> None:
    """Transport-provided secure prompt callbacks should be forwarded unchanged."""

    captured_callbacks: list[tuple[object | None, object | None, object | None]] = []

    async def _fake_run_turn_with_secure_resolution(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink=None,
        allow_secure_prompt: bool,
        turn_overrides=None,
        confirm_space_fn=None,
        tool_not_allowed_prompt_fn=None,
        credential_profile_prompt_fn=None,
    ):
        _ = message, profile_id, session_id, progress_sink, allow_secure_prompt, turn_overrides
        captured_callbacks.append(
            (
                confirm_space_fn,
                tool_not_allowed_prompt_fn,
                credential_profile_prompt_fn,
            )
        )
        return TurnResult(
            run_id=1,
            session_id="s-forward-prompts",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    async def _confirm_space(**_: object) -> bool:
        return True

    async def _tool_prompt(**_: object) -> str:
        return "allow_once"

    async def _profile_prompt(*_: object, **__: object) -> str | None:
        return "default"

    outcome = await run_chat_turn_with_optional_planning(
        message="hello",
        profile_id="default",
        session_id="s-forward-prompts",
        progress_sink=None,
        allow_secure_prompt=True,
        run_turn_with_secure_resolution=_fake_run_turn_with_secure_resolution,
        planning_mode=None,
        thinking_level=None,
        confirm_space_fn=_confirm_space,
        tool_not_allowed_prompt_fn=_tool_prompt,
        credential_profile_prompt_fn=_profile_prompt,
    )

    assert outcome.result.envelope.message == "done"
    assert captured_callbacks == [(_confirm_space, _tool_prompt, _profile_prompt)]
