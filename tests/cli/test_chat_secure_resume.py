"""Tests for secure-flow replay-safe resume in chat command."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pytest import CaptureFixture, MonkeyPatch

from afkbot.cli.commands.chat_secure_flow import (
    _render_security_prompt,
    build_run_turn_with_overrides,
    run_turn_with_secure_resolution,
    _tool_not_allowed_question_text,
)
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.pending_envelopes import (
    PROFILE_SELECTION_QUESTION_KIND,
    TOOL_NOT_ALLOWED_QUESTION_KIND,
)
from afkbot.services.agent_loop.safety_policy import CONFIRM_ACK_PARAM, CONFIRM_QID_PARAM
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.tools.base import ToolCall
from afkbot.settings import get_settings


async def test_secure_flow_resumes_with_planned_tool_call(monkeypatch: MonkeyPatch) -> None:
    """Secure submit should resume from pending tool call, not rerun original message."""

    calls: list[tuple[str, list[ToolCall] | None]] = []

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append((message, planned_tool_calls))
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="request_secure_field",
                    message="secure",
                    question_id="q-1",
                    secure_field="telegram_token",
                    spec_patch={
                        "tool_name": "app.run",
                        "tool_params": {
                            "app_name": "telegram",
                            "action": "send_message",
                            "params": {"text": "hello"},
                        },
                        "integration_name": "telegram",
                        "credential_name": "telegram_token",
                        "credential_profile_key": "default",
                        "secure_nonce": "nonce-1",
                    },
                ),
            )
        assert message == "secure_resume:app.run"
        assert planned_tool_calls is not None
        assert planned_tool_calls[0].name == "app.run"
        assert planned_tool_calls[0].params["params"]["text"] == "hello"
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    async def _fake_submit_secure_field(
        *,
        profile_id: str,  # noqa: ARG001
        envelope: ActionEnvelope,  # noqa: ARG001
        secret_value: str,  # noqa: ARG001
        session_id: str | None = None,  # noqa: ARG001
    ) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", lambda *args, **kwargs: "secret")

    result = await run_turn_with_secure_resolution(
        message="send telegram",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        submit_secure_field_fn=_fake_submit_secure_field,
    )

    assert result.envelope.action == "finalize"
    assert len(calls) == 2
    assert calls[0][0] == "send telegram"
    assert calls[0][1] is None


async def test_secure_flow_preserves_secret_whitespace(monkeypatch: MonkeyPatch) -> None:
    """Secure prompt should submit exact secret value without trimming."""

    submitted: dict[str, str] = {}
    calls = {"count": 0}

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="request_secure_field",
                    message="secure",
                    question_id="q-1",
                    secure_field="test_secret",
                    spec_patch={"secure_nonce": "nonce-1"},
                ),
            )
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    async def _fake_submit_secure_field(
        *,
        profile_id: str,  # noqa: ARG001
        envelope: ActionEnvelope,  # noqa: ARG001
        secret_value: str,
        session_id: str | None = None,  # noqa: ARG001
    ) -> tuple[bool, str]:
        submitted["value"] = secret_value
        return True, "ok"

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", lambda *args, **kwargs: "  value  ")

    result = await run_turn_with_secure_resolution(
        message="save secret",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        submit_secure_field_fn=_fake_submit_secure_field,
    )

    assert result.envelope.action == "finalize"
    assert calls["count"] == 2
    assert submitted["value"] == "  value  "


def test_render_security_prompt_shows_security_context(capsys: CaptureFixture[str]) -> None:
    """Security prompt should show secure-input context without legacy step counters."""

    _render_security_prompt(
        ActionEnvelope(
            action="request_secure_field",
            message="secure",
            secure_field="telegram_token",
            spec_patch={
                "integration_name": "telegram",
                "credential_profile_key": "default",
                "credential_name": "telegram_token",
            },
        ),
    )
    out = capsys.readouterr().out
    assert "AFK Agent (security)" in out
    assert "Secure input required" in out


def test_tool_not_allowed_question_text_includes_tool_params_and_reason() -> None:
    """CLI question helper should render a concise tool-access prompt."""

    text = _tool_not_allowed_question_text(
        ActionEnvelope(
            action="ask_question",
            message="Tool access requires explicit approval before execution.",
            spec_patch={
                "tool_name": "bash.exec",
                "tool_params": {"cwd": ".", "command": "ls"},
                "tool_not_allowed_reason": "Tool not available in current turn: bash.exec",
                "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
            },
        )
    )

    assert "Approve access to tool: bash.exec?" in text
    assert "Proposed parameters:" in text
    assert "command: ls" in text
    assert "Reason: Tool not available in current turn: bash.exec" in text


async def test_build_run_turn_without_bound_overrides_rejects_unexpected_runtime_kwarg() -> None:
    """Unbound secure-flow runner should keep the same explicit kwarg surface."""

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    run_turn = build_run_turn_with_overrides(
        None,
        run_once_result_fn=_fake_run_once_result,
    )

    with pytest.raises(TypeError):
        await run_turn(  # type: ignore[misc]
            message="hello",
            profile_id="default",
            session_id="s",
            progress_sink=None,
            allow_secure_prompt=False,
            runtime_overrides=TurnContextOverrides(prompt_overlay="unexpected"),
        )


async def test_secure_flow_limit_allows_last_valid_step_then_blocks_next(
    monkeypatch: MonkeyPatch,
) -> None:
    """Secure-flow limit should allow exactly N prompts and block prompt N+1."""

    get_settings.cache_clear()
    monkeypatch.setenv("AFKBOT_SECURE_FLOW_MAX_STEPS", "1")
    get_settings.cache_clear()

    call_count = 0
    prompt_count = 0

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="request_secure_field",
                    message="secure",
                    question_id="q-1",
                    secure_field="token-1",
                    spec_patch={
                        "tool_name": "credentials.create",
                        "tool_params": {"credential_name": "first"},
                        "secure_nonce": "nonce-1",
                    },
                ),
            )
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="request_secure_field",
                message="secure",
                question_id="q-2",
                secure_field="token-2",
                spec_patch={
                    "tool_name": "credentials.update",
                    "tool_params": {"credential_name": "second"},
                    "secure_nonce": "nonce-2",
                },
            ),
        )

    async def _fake_submit_secure_field(
        *,
        profile_id: str,  # noqa: ARG001
        envelope: ActionEnvelope,  # noqa: ARG001
        secret_value: str,  # noqa: ARG001
        session_id: str | None = None,  # noqa: ARG001
    ) -> tuple[bool, str]:
        return True, "ok"

    def _fake_prompt(*args: object, **kwargs: object) -> str:  # noqa: ANN003
        nonlocal prompt_count
        prompt_count += 1
        return "secret"

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", _fake_prompt)

    result = await run_turn_with_secure_resolution(
        message="start",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        submit_secure_field_fn=_fake_submit_secure_field,
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "interactive_flow_limit_reached"
    assert call_count == 2
    assert prompt_count == 1
    get_settings.cache_clear()


async def test_secure_flow_returns_request_when_prompt_disabled(monkeypatch: MonkeyPatch) -> None:
    """When secure prompt is disabled, command should return secure envelope untouched."""

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="request_secure_field",
                message="secure",
                question_id="q-1",
                secure_field="telegram_token",
                spec_patch={"secure_nonce": "nonce-1"},
            ),
        )

    async def _fake_submit_secure_field(
        *,
        profile_id: str,  # noqa: ARG001
        envelope: ActionEnvelope,  # noqa: ARG001
        secret_value: str,  # noqa: ARG001
        session_id: str | None = None,  # noqa: ARG001
    ) -> tuple[bool, str]:
        raise AssertionError("submit_secure_field must not be called when prompts are disabled")

    def _fail_prompt(*args: object, **kwargs: object) -> str:  # noqa: ANN002, ANN003
        raise AssertionError("prompt must not be called")

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", _fail_prompt)

    result = await run_turn_with_secure_resolution(
        message="save creds",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=False,
        run_once_result_fn=_fake_run_once_result,
        submit_secure_field_fn=_fake_submit_secure_field,
    )

    assert result.envelope.action == "request_secure_field"
    assert result.envelope.secure_field == "telegram_token"
    assert result.envelope.question_id == "q-1"


async def test_secure_flow_blocks_when_submit_returns_error(monkeypatch: MonkeyPatch) -> None:
    """Secure submit failure should stop flow and return deterministic block envelope."""

    run_calls = 0

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        nonlocal run_calls
        run_calls += 1
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="request_secure_field",
                message="secure",
                question_id="q-1",
                secure_field="telegram_token",
                spec_patch={"secure_nonce": "nonce-1"},
            ),
        )

    async def _fake_submit_secure_field(
        *,
        profile_id: str,  # noqa: ARG001
        envelope: ActionEnvelope,  # noqa: ARG001
        secret_value: str,  # noqa: ARG001
        session_id: str | None = None,  # noqa: ARG001
    ) -> tuple[bool, str]:
        return False, "secure_request_invalid_or_expired"

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", lambda *args, **kwargs: "secret")

    result = await run_turn_with_secure_resolution(
        message="save creds",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        submit_secure_field_fn=_fake_submit_secure_field,
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "secure_request_invalid_or_expired"
    assert run_calls == 1


async def test_secure_flow_without_resume_tool_finishes_after_store(
    monkeypatch: MonkeyPatch,
) -> None:
    """When secure envelope has no replay target, flow should continue automatically."""

    calls: list[str] = []

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append(message)
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="request_secure_field",
                    message="secure",
                    question_id="q-1",
                    secure_field="telegram_token",
                    spec_patch={
                        "tool_name": "",
                        "tool_params": None,
                        "integration_name": "telegram",
                        "credential_name": "telegram_token",
                        "credential_profile_key": "default",
                        "secure_nonce": "nonce-1",
                    },
                ),
            )
        assert message.startswith("secure_resume: a required credential was captured via secure input.")
        assert "integration=telegram" in message
        assert "field=telegram_token" in message
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    async def _fake_submit_secure_field(
        *,
        profile_id: str,  # noqa: ARG001
        envelope: ActionEnvelope,  # noqa: ARG001
        secret_value: str,  # noqa: ARG001
        session_id: str | None = None,  # noqa: ARG001
    ) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", lambda *args, **kwargs: "secret")

    result = await run_turn_with_secure_resolution(
        message="save creds",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        submit_secure_field_fn=_fake_submit_secure_field,
    )

    assert result.envelope.action == "finalize"
    assert result.envelope.message == "done"
    assert len(calls) == 2


async def test_approval_question_resumes_with_planned_tool_call(monkeypatch: MonkeyPatch) -> None:
    """ask_question envelope should prompt yes/no and resume tool call on approval."""

    calls: list[tuple[str, list[ToolCall] | None]] = []

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append((message, planned_tool_calls))
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="confirm",
                    question_id="approval-1",
                    spec_patch={
                        "tool_name": "debug.echo",
                        "tool_params": {"message": "ok"},
                        "approval_mode": "strict",
                    },
                ),
            )
        assert message == "approval_resume:debug.echo"
        assert planned_tool_calls is not None
        assert planned_tool_calls[0].name == "debug.echo"
        assert planned_tool_calls[0].params[CONFIRM_ACK_PARAM] is True
        assert planned_tool_calls[0].params[CONFIRM_QID_PARAM] == "approval-1"
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    result = await run_turn_with_secure_resolution(
        message="remove file",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        confirm_space_fn=lambda **kwargs: True,
    )

    assert result.envelope.action == "finalize"
    assert result.envelope.message == "done"
    assert len(calls) == 2


async def test_approval_question_denied_returns_finalize(monkeypatch: MonkeyPatch) -> None:
    """ask_question envelope should stop workflow when user denies confirmation."""

    calls = 0

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        nonlocal calls
        calls += 1
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="confirm",
                question_id="approval-1",
                spec_patch={"tool_name": "debug.echo", "tool_params": {"message": "ok"}},
            ),
        )

    result = await run_turn_with_secure_resolution(
        message="remove file",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        confirm_space_fn=lambda **kwargs: False,
    )

    assert calls == 1
    assert result.envelope.action == "finalize"
    assert "cancelled" in result.envelope.message.lower()


async def test_profile_selection_question_resumes_tool_with_selected_profile(
    monkeypatch: MonkeyPatch,
) -> None:
    """Credential profile question should resume tool call with chosen `profile_name`."""

    calls: list[tuple[str, list[ToolCall] | None]] = []

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append((message, planned_tool_calls))
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="choose profile",
                    question_id="profile-1",
                    spec_patch={
                        "question_kind": PROFILE_SELECTION_QUESTION_KIND,
                        "tool_name": "app.run",
                        "tool_params": {
                            "app_name": "telegram",
                            "action": "get_me",
                            "params": {},
                        },
                        "integration_name": "telegram",
                        "credential_name": "telegram_token",
                        "available_profile_keys": ["work", "personal"],
                    },
                ),
            )
        assert message == "profile_resume:app.run"
        assert planned_tool_calls is not None
        assert planned_tool_calls[0].name == "app.run"
        assert planned_tool_calls[0].params["profile_name"] == "work"
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    monkeypatch.setattr("afkbot.cli.commands.chat_secure_flow.typer.prompt", lambda *args, **kwargs: "work")

    result = await run_turn_with_secure_resolution(
        message="send telegram",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
    )

    assert result.envelope.action == "finalize"
    assert len(calls) == 2


async def test_profile_selection_question_missing_profiles_returns_block(
    monkeypatch: MonkeyPatch,
) -> None:
    """Missing available profiles should block gracefully instead of raising in CLI."""

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="choose profile",
                question_id="profile-1",
                spec_patch={
                    "question_kind": PROFILE_SELECTION_QUESTION_KIND,
                    "tool_name": "app.run",
                    "tool_params": {"app_name": "telegram", "action": "get_me"},
                    "integration_name": "telegram",
                    "credential_name": "telegram_token",
                },
            ),
        )

    result = await run_turn_with_secure_resolution(
        message="send telegram",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "credential_profile_choices_missing"


async def test_approval_question_limit_blocks_repeated_prompts(monkeypatch: MonkeyPatch) -> None:
    """Repeated ask-question resumes should stop once the interactive step limit is reached."""

    get_settings.cache_clear()
    monkeypatch.setenv("AFKBOT_SECURE_FLOW_MAX_STEPS", "1")
    get_settings.cache_clear()

    run_calls = 0
    confirm_calls = 0

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
    ) -> TurnResult:
        nonlocal run_calls
        run_calls += 1
        return TurnResult(
            run_id=run_calls,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="confirm",
                question_id=f"approval-{run_calls}",
                spec_patch={
                    "tool_name": "debug.echo",
                    "tool_params": {"message": "ok"},
                    "approval_mode": "strict",
                },
            ),
        )

    def _fake_confirm(**kwargs: object) -> bool:  # noqa: ANN003
        nonlocal confirm_calls
        confirm_calls += 1
        return True

    result = await run_turn_with_secure_resolution(
        message="remove file",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        confirm_space_fn=_fake_confirm,
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "interactive_flow_limit_reached"
    assert run_calls == 2
    assert confirm_calls == 1
    get_settings.cache_clear()


async def test_confirmation_prompt_accepts_async_callback() -> None:
    """Async approval callback should be awaited and keep secure flow progressing."""

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="confirm",
                question_id="approval-1",
                spec_patch={
                    "tool_name": "debug.echo",
                    "tool_params": {"message": "ok"},
                    "approval_mode": "strict",
                },
            ),
        )

    async def _async_confirm(**kwargs: object) -> bool:  # noqa: ARG003
        return True

    result = await run_turn_with_secure_resolution(
        message="remove file",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        confirm_space_fn=_async_confirm,
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "interactive_flow_limit_reached"


async def test_tool_not_allowed_question_allow_session_updates_session_approved_tools(
    monkeypatch: MonkeyPatch,
) -> None:
    """Tool-not-allowed decision with session scope should remember approved tools."""

    calls: list[tuple[str, list[ToolCall] | None]] = []
    session_approved_tools: set[str] = set()

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append((message, planned_tool_calls))
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="tool not allowed",
                    question_id="tool_not_allowed-1",
                    spec_patch={
                        "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                        "tool_name": "bash.exec",
                        "tool_params": {"cmd": "echo ok", "cwd": "."},
                    },
                ),
            )
        assert message.startswith("tool_access_resume:")
        assert planned_tool_calls is None
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=lambda **kwargs: "allow_session",
        session_approved_tools=session_approved_tools,
    )

    assert result.envelope.action == "finalize"
    assert result.envelope.message == "done"
    assert len(calls) == 2
    assert session_approved_tools == {"bash.exec"}


async def test_tool_not_allowed_question_with_stable_signature_blocks_retries_for_same_tool_params() -> None:
    """Tool-not-allowed prompt should not loop indefinitely when tool is still rejected."""

    call_count = 0

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        del message, profile_id, session_id, progress_sink, context_overrides, planned_tool_calls
        nonlocal call_count
        call_count += 1
        return TurnResult(
            run_id=call_count,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="tool not allowed",
                question_id=f"tool_not_allowed-{call_count}",
                spec_patch={
                    "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                    "tool_name": "bash.exec",
                    "tool_params": {"command": "ls", "cwd": "."},
                },
            ),
        )

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=lambda **kwargs: "allow_once",
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "interactive_flow_limit_reached"
    assert call_count == 2


async def test_tool_not_allowed_question_allow_once_applies_one_time_runtime_approval() -> None:
    """Allow once should pass temporary approval metadata even without session approvals."""

    calls: list[tuple[str, list[str] | None]] = []

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        runtime_allowed_tools: list[str] | None = None
        if context_overrides is not None:
            if context_overrides.approved_tool_names:
                runtime_allowed_tools = sorted(context_overrides.approved_tool_names)
        calls.append(
            (
                message,
                runtime_allowed_tools,
            )
        )

        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="tool not allowed",
                    question_id="tool_not_allowed-1",
                    spec_patch={
                        "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                        "tool_name": "bash.exec",
                        "tool_params": {"cmd": "echo ok", "cwd": "."},
                    },
                ),
            )
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=lambda **kwargs: "allow_once",
    )

    assert result.envelope.action == "finalize"
    assert calls[0][1] is None
    assert calls[1][1] == ["bash.exec"]


async def test_tool_not_allowed_question_allow_once_executes_without_session_approved_tool_change(
    monkeypatch: MonkeyPatch,
) -> None:
    """Tool-not-allowed decision 'once' should execute and not require session storage."""

    calls: list[tuple[str, list[ToolCall] | None]] = []
    session_approved_tools: set[str] = set(["other.tool"])

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append((message, planned_tool_calls))
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="tool not allowed",
                    question_id="tool_not_allowed-1",
                    spec_patch={
                        "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                        "tool_name": "bash.exec",
                        "tool_params": {"cmd": "echo ok", "cwd": "."},
                    },
                ),
            )
        assert message.startswith("tool_access_resume:")
        assert planned_tool_calls is None
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=lambda **kwargs: "allow_once",
        session_approved_tools=session_approved_tools,
    )

    assert result.envelope.action == "finalize"
    assert len(calls) == 2
    assert session_approved_tools == {"other.tool"}


async def test_tool_not_allowed_question_stops_live_progress_before_prompt() -> None:
    """Interactive prompts should ask the transport to stop live progress rendering first."""

    class _ProgressSink:
        def __init__(self) -> None:
            self.stopped = 0

        def __call__(self, _event: object) -> None:
            return None

        def before_interactive_prompt(self) -> None:
            self.stopped += 1

    progress_sink = _ProgressSink()

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="tool not allowed",
                question_id="tool_not_allowed-1",
                spec_patch={
                    "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                    "tool_name": "bash.exec",
                    "tool_params": {"cmd": "ls"},
                },
            ),
        )

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=progress_sink,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=lambda **kwargs: "deny",
    )

    assert result.envelope.action == "finalize"
    assert progress_sink.stopped == 1


async def test_tool_not_allowed_question_supports_async_prompt_callback() -> None:
    """Async callback for tool-not-allowed prompt should be awaited and honored."""

    calls: list[tuple[str, list[ToolCall] | None]] = []

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        calls.append((message, planned_tool_calls))
        if len(calls) == 1:
            return TurnResult(
                run_id=1,
                session_id="s",
                profile_id="default",
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="tool not allowed",
                    question_id="tool_not_allowed-1",
                    spec_patch={
                        "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                        "tool_name": "bash.exec",
                        "tool_params": {"cmd": "echo ok", "cwd": "."},
                    },
                ),
            )
        assert message.startswith("tool_access_resume:")
        assert planned_tool_calls is None
        return TurnResult(
            run_id=2,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    async def _choose_once(**kwargs: object) -> str:  # noqa: ARG003
        return "allow_once"

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=_choose_once,
    )

    assert result.envelope.action == "finalize"
    assert result.envelope.message == "done"
    assert len(calls) == 2


async def test_tool_not_allowed_question_denied_stops_turn() -> None:
    """Tool-not-allowed branch should stop immediately when user denies execution."""

    async def _fake_run_once_result(
        *,
        message: str,  # noqa: ARG001
        profile_id: str,  # noqa: ARG001
        session_id: str,  # noqa: ARG001
        planned_tool_calls: list[ToolCall] | None = None,  # noqa: ARG001
        progress_sink: Callable[[object], None] | None = None,  # noqa: ARG001
        context_overrides: TurnContextOverrides | None = None,  # noqa: ARG001
    ) -> TurnResult:
        return TurnResult(
            run_id=1,
            session_id="s",
            profile_id="default",
            envelope=ActionEnvelope(
                action="ask_question",
                message="tool not allowed",
                question_id="tool_not_allowed-1",
                spec_patch={
                    "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                    "tool_name": "bash.exec",
                    "tool_params": {"cmd": "echo bad", "cwd": "."},
                },
            ),
        )

    result = await run_turn_with_secure_resolution(
        message="inspect files",
        profile_id="default",
        session_id="s",
        progress_sink=None,
        allow_secure_prompt=True,
        run_once_result_fn=_fake_run_once_result,
        tool_not_allowed_prompt_fn=lambda **kwargs: "deny",
        session_approved_tools=set(),
    )

    assert result.envelope.action == "finalize"
    assert "cancelled" in result.envelope.message.lower()
