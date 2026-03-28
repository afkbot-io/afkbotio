"""Security tests for sensitive-field redaction in the canonical loop sanitizer."""

from __future__ import annotations

from typing import cast

from afkbot.services.agent_loop.loop_sanitizer import sanitize_value


def test_sanitize_value_redacts_sensitive_field_names() -> None:
    """Sensitive field names should always be redacted, excluding error_code."""

    payload = {
        "secret_value": "plain",
        "token": "plain",
        "password": "plain",
        "api_key": "plain",
        "authorization": "plain",
        "value": "plain",
        "nested": {"mySecret": "plain", "inner": {"access_token": "plain"}},
        "error_code": "token_should_not_be_redacted",
    }

    sanitized = cast(dict[str, object], sanitize_value(payload))
    nested = cast(dict[str, object], sanitized["nested"])
    inner = cast(dict[str, object], nested["inner"])

    assert sanitized["secret_value"] == "[REDACTED]"
    assert sanitized["token"] == "[REDACTED]"
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["authorization"] == "[REDACTED]"
    assert sanitized["value"] == "[REDACTED]"
    assert nested["mySecret"] == "[REDACTED]"
    assert inner["access_token"] == "[REDACTED]"
    assert sanitized["error_code"] == "token_should_not_be_redacted"


def test_sanitize_value_keeps_token_like_masking() -> None:
    """Token-like strings should stay masked for non-sensitive field names."""

    sanitized = cast(
        dict[str, object],
        sanitize_value({"message": "abc abcdefgh12345678ijklmnop"}),
    )

    assert sanitized["message"] == "abc [REDACTED]"
