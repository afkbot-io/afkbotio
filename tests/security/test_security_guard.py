"""Unit tests for central security guard logic."""

from __future__ import annotations

from afkbot.services.agent_loop.security_guard import SecurityGuard
from afkbot.services.tools.base import ToolCall


def test_user_message_blocked_for_secret_assignment() -> None:
    """User message must block explicit secret assignments from user text."""

    guard = SecurityGuard()
    decision = guard.check_user_message(text="my token=abc123")

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"
    assert "[REDACTED]" in decision.redacted_text
    assert "abc123" not in decision.redacted_text


def test_user_message_blocks_natural_secret_assignment_with_redaction() -> None:
    """Secret input is blocked and redacted in chat/runtime flow."""

    guard = SecurityGuard()
    decision = guard.check_user_message(text="password: qwerty")

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"
    assert "qwerty" not in decision.redacted_text
    assert "[REDACTED]" in decision.redacted_text


def test_assistant_message_blocked() -> None:
    """Assistant secret-like output must be blocked."""

    guard = SecurityGuard()
    decision = guard.check_assistant_message(
        text="Use this api_key=supersecretvalue",
    )

    assert decision.allow is False
    assert decision.error_code == "security_secret_output_blocked"
    assert "[REDACTED]" in decision.redacted_text
    assert "supersecretvalue" not in decision.redacted_text


def test_natural_language_secret_phrase_is_blocked() -> None:
    """Natural language secret disclosure should be blocked and redacted."""

    guard = SecurityGuard()
    decision = guard.check_user_message(
        text="my password is qwerty",
    )

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"
    assert "qwerty" not in decision.redacted_text
    assert "[REDACTED]" in decision.redacted_text


def test_redacted_marker_does_not_disable_secret_detection() -> None:
    """Presence of [REDACTED] text should not bypass secret detection."""

    guard = SecurityGuard()
    decision = guard.check_user_message(
        text="[REDACTED] token=abc123",
    )

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"


def test_redacted_marker_before_real_secret_still_blocks() -> None:
    """Guard must block values where [REDACTED] is followed by plaintext secret."""

    guard = SecurityGuard()
    decision = guard.check_user_message(
        text="my password is [REDACTED] qwerty",
    )

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"


def test_telegram_bot_token_string_is_blocked() -> None:
    """Telegram bot token format should be blocked as secret-like input."""

    guard = SecurityGuard()
    decision = guard.check_user_message(
        text="8578386608:AAE85-6Dg6go7OXO-y2Uz5WMn56aTAEAoBY",
    )

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"
    assert "[REDACTED]" in decision.redacted_text


def test_long_mixed_alnum_secret_is_blocked() -> None:
    """Generic long mixed alnum token should be blocked."""

    guard = SecurityGuard()
    decision = guard.check_user_message(
        text="Use token ZXCVBN1234QWER5678TYUI9012ASDF",
    )

    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"
    assert "ZXCVBN1234QWER5678TYUI9012ASDF" not in decision.redacted_text


def test_long_mixed_alnum_without_keyword_context_is_allowed() -> None:
    """Long mixed tokens should pass without surrounding secret context."""

    # Arrange
    guard = SecurityGuard()

    # Act
    decision = guard.check_user_message(
        text="Сессия продолжается с кодом ZXCVBN1234QWER5678TYUI9012ASDF",
    )

    # Assert
    assert decision.allow is True
    assert decision.error_code is None
    assert decision.redacted_text == "Сессия продолжается с кодом ZXCVBN1234QWER5678TYUI9012ASDF"


def test_shorter_mixed_alnum_with_keyword_context_is_blocked() -> None:
    """20+ char tokens should still block when secret context is present."""

    # Arrange
    guard = SecurityGuard()

    # Act
    decision = guard.check_user_message(
        text="Use token ZXCVBN1234QWER5678TYUI",
    )

    # Assert
    assert decision.allow is False
    assert decision.error_code == "security_secret_input_blocked"
    assert "ZXCVBN1234QWER5678TYUI" not in decision.redacted_text


def test_guard_tool_call_blocks_credentials_write_with_plaintext_secret() -> None:
    """credentials.create must require secure field capture when value is provided."""

    guard = SecurityGuard()
    call = ToolCall(
        name="credentials.create",
        params={
            "app_name": "telegram",
            "profile_name": "default",
            "credential_slug": "telegram_token",
            "value": "short-secret",
        },
    )
    guarded = guard.guard_tool_call(call=call)

    assert guarded.allow is False
    assert guarded.error_code == "security_secure_input_required"
    assert guarded.execution_call.params["value"] == "short-secret"
    assert guarded.log_call.params["value"] == "[REDACTED]"
