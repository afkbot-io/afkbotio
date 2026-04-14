"""Pure sanitizer helpers extracted from AgentLoop orchestration module."""

from __future__ import annotations

from afkbot.services.agent_loop.security_guard import SecurityGuard

_SENSITIVE_FIELD_PARTS = ("secret", "token", "password", "api_key", "authorization")
_NON_SECRET_ID_FIELDS = frozenset({"id", "task_id", "tool_call_id", "call_id", "question_id"})
_SENSITIVE_VALUE_HINT_FIELDS = frozenset({"name", "field", "key", "slug", "credential_name", "credential_slug"})
_SECURITY_GUARD = SecurityGuard()


def sanitize(value: str) -> str:
    """Preserve plain text for task/runtime prompts and logs."""

    return value


def sanitize_value(value: object, *, field_name: str | None = None) -> object:
    """Mask only explicitly secret payload content in nested values."""

    if field_name == "error_code":
        return value
    if field_name is not None and field_name.lower() in _NON_SECRET_ID_FIELDS:
        return value
    if is_sensitive_field(field_name):
        return "[REDACTED]"
    if isinstance(value, str):
        return _SECURITY_GUARD.redact_text(value)
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        redact_value_field = _mapping_has_sensitive_value_hint(value)
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() == "value" and redact_value_field and item is not None
                else sanitize_value(item, field_name=str(key))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, (set, tuple)):
        return [sanitize_value(item) for item in value]
    return _SECURITY_GUARD.redact_text(repr(value))


def is_sensitive_field(field_name: str | None) -> bool:
    """Return true when field name indicates sensitive data content."""

    if field_name is None:
        return False
    lowered = field_name.lower()
    return any(part in lowered for part in _SENSITIVE_FIELD_PARTS)


def _mapping_has_sensitive_value_hint(value: dict[object, object]) -> bool:
    for key, item in value.items():
        if str(key).lower() not in _SENSITIVE_VALUE_HINT_FIELDS:
            continue
        if isinstance(item, str) and is_sensitive_field(item):
            return True
    return False


def to_params_dict(value: object) -> dict[str, object]:
    """Normalize unknown object into dict with string keys."""

    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def to_payload_dict(value: object) -> dict[str, object]:
    """Normalize unknown payload object into dict with string keys."""

    if not isinstance(value, dict):
        return {"value": value}
    return {str(key): item for key, item in value.items()}


def tool_log_payload(
    *,
    tool_name: str,
    payload: dict[str, object],
    redact_fields: frozenset[str],
) -> dict[str, object]:
    """Build runlog payload with optional per-tool nested field redaction."""

    sanitized = to_payload_dict(sanitize_value(payload))
    if not tool_name.startswith("memory."):
        return sanitized
    redacted = redact_fields_by_name(sanitized, field_names=redact_fields)
    if isinstance(redacted, dict):
        return {str(key): item for key, item in redacted.items()}
    return sanitized


def redact_fields_by_name(
    value: object,
    *,
    field_names: frozenset[str],
    field_name: str | None = None,
) -> object:
    """Recursively redact configured field names in nested payloads."""

    if field_name is not None and field_name.lower() in field_names:
        if value is None:
            return None
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(key): redact_fields_by_name(
                item,
                field_names=field_names,
                field_name=str(key),
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_fields_by_name(item, field_names=field_names) for item in value]
    if isinstance(value, tuple):
        return [redact_fields_by_name(item, field_names=field_names) for item in value]
    if isinstance(value, set):
        return [redact_fields_by_name(item, field_names=field_names) for item in value]
    return value
