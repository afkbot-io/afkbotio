"""Unit tests for approval/secure pending envelope helpers."""

from __future__ import annotations

from afkbot.services.agent_loop.pending_envelopes import (
    PROFILE_SELECTION_QUESTION_KIND,
    PendingEnvelopeBuilder,
)
from afkbot.services.agent_loop.safety_policy import (
    APPROVAL_REQUIRED_ERROR_CODE,
    CONFIRM_ACK_PARAM,
    CONFIRM_QID_PARAM,
)
from afkbot.services.tools.base import ToolCall, ToolResult


def test_build_approval_envelope_strips_internal_confirmation_markers() -> None:
    """Approval envelope should not expose internal resume markers back to the model/user."""

    builder = PendingEnvelopeBuilder(params_normalizer=dict)
    tool_calls = [
        ToolCall(
            name="bash.exec",
            params={
                "cmd": "rm -rf tmp/data.txt",
                "cwd": ".",
                CONFIRM_ACK_PARAM: True,
                CONFIRM_QID_PARAM: "approval:old",
            },
        )
    ]
    tool_results = [
        ToolResult.error(
            error_code=APPROVAL_REQUIRED_ERROR_CODE,
            reason="need confirmation",
            metadata={"approval_mode": "strict", "approval_reason": "need confirmation"},
        )
    ]

    envelope = builder.build_approval_envelope(tool_calls=tool_calls, tool_results=tool_results)

    assert envelope is not None
    assert envelope.action == "ask_question"
    assert envelope.question_id is not None
    assert envelope.spec_patch == {
        "tool_name": "bash.exec",
        "tool_params": {"cmd": "rm -rf tmp/data.txt", "cwd": "."},
        "approval_mode": "strict",
        "approval_reason": "need confirmation",
    }


def test_build_secure_envelope_disables_resume_for_secure_submit_errors() -> None:
    """Secret-input errors should request input without replaying the original tool call."""

    builder = PendingEnvelopeBuilder(params_normalizer=dict)
    tool_calls = [
        ToolCall(
            name="credentials.request",
            params={
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "profile_name": "ops",
            },
        )
    ]
    tool_results = [
        ToolResult.error(
            error_code="security_secure_input_required",
            reason="secure input required",
        )
    ]

    envelope = builder.build_secure_envelope(tool_calls=tool_calls, tool_results=tool_results)

    assert envelope is not None
    assert envelope.action == "request_secure_field"
    assert envelope.secure_field == "telegram_token"
    assert envelope.question_id is not None
    assert envelope.spec_patch is not None
    assert envelope.spec_patch["tool_name"] == ""
    assert envelope.spec_patch["tool_params"] is None
    assert envelope.spec_patch["integration_name"] == "telegram"
    assert envelope.spec_patch["credential_name"] == "telegram_token"
    assert envelope.spec_patch["credential_profile_key"] == "ops"
    assert envelope.spec_patch["error_code"] == "security_secure_input_required"
    assert isinstance(envelope.spec_patch["secure_nonce"], str)


def test_build_profile_selection_envelope_uses_ask_question_instead_of_secure_prompt() -> None:
    """Profile ambiguity should request profile selection, not secret input."""

    builder = PendingEnvelopeBuilder(params_normalizer=dict)
    tool_calls = [
        ToolCall(
            name="app.run",
            params={
                "app_name": "telegram",
                "action": "get_me",
                "params": {},
            },
        )
    ]
    tool_results = [
        ToolResult.error(
            error_code="credential_profile_required",
            reason="Multiple credential profiles available",
            metadata={
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "available_profile_keys": ["work", "personal"],
            },
        )
    ]

    envelope = builder.build_profile_selection_envelope(tool_calls=tool_calls, tool_results=tool_results)

    assert envelope is not None
    assert envelope.action == "ask_question"
    assert envelope.secure_field is None
    assert envelope.question_id is not None
    assert envelope.spec_patch == {
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
        "error_code": "credential_profile_required",
    }


def test_build_profile_selection_envelope_handles_single_available_profile() -> None:
    """Deleted explicit profile should not render a misleading multiple-choice message."""

    builder = PendingEnvelopeBuilder(params_normalizer=dict)
    tool_calls = [
        ToolCall(
            name="app.run",
            params={
                "app_name": "http",
                "action": "request",
                "params": {},
                "credential_profile_key": "ops",
            },
        )
    ]
    tool_results = [
        ToolResult.error(
            error_code="credential_profile_required",
            reason="Requested credential profile is unavailable",
            metadata={
                "integration_name": "http",
                "credential_name": "api_key",
                "requested_profile_key": "ops",
                "available_profile_keys": ["default"],
            },
        )
    ]

    envelope = builder.build_profile_selection_envelope(tool_calls=tool_calls, tool_results=tool_results)

    assert envelope is not None
    assert envelope.action == "ask_question"
    assert envelope.message == (
        "Credential profile 'ops' is unavailable for integration 'http' and credential "
        "'api_key'. Choose available profile 'default' to continue."
    )
    assert envelope.spec_patch == {
        "question_kind": PROFILE_SELECTION_QUESTION_KIND,
        "tool_name": "app.run",
        "tool_params": {
            "app_name": "http",
            "action": "request",
            "params": {},
            "credential_profile_key": "ops",
        },
        "integration_name": "http",
        "credential_name": "api_key",
        "available_profile_keys": ["default"],
        "error_code": "credential_profile_required",
    }
