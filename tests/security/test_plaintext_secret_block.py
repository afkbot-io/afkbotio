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
    assert nested["mySecret"] == "[REDACTED]"
    assert inner["access_token"] == "[REDACTED]"
    assert sanitized["error_code"] == "token_should_not_be_redacted"


def test_sanitize_value_keeps_non_secret_branch_and_ref_text() -> None:
    """Non-secret task-flow text should stay intact instead of becoming [REDACTED]."""

    sanitized = cast(
        dict[str, object],
        sanitize_value(
            {
                "message": (
                    "Clone or update the branch codex/managed-runtime-service-v1-0-13 "
                    "from afkbot-io/afkbotio for pull request #33."
                )
            }
        ),
    )

    assert sanitized["message"] == (
        "Clone or update the branch codex/managed-runtime-service-v1-0-13 "
        "from afkbot-io/afkbotio for pull request #33."
    )


def test_sanitize_value_redacts_value_when_sibling_name_marks_secret() -> None:
    """Generic `{name, value}` secret payloads should still redact the secret value."""

    sanitized = cast(
        dict[str, object],
        sanitize_value(
            {
                "inputs": [
                    {
                        "name": "telegram_token",
                        "value": "short-secret",
                    }
                ]
            }
        ),
    )

    inputs = cast(list[object], sanitized["inputs"])
    assert inputs == [{"name": "telegram_token", "value": "[REDACTED]"}]
