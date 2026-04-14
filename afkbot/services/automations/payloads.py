"""Webhook payload sanitization and idempotency helpers."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from hashlib import sha256

from afkbot.services.agent_loop.security_guard import SecurityGuard

_SENSITIVE_FIELD_PARTS = ("secret", "token", "password", "api_key", "authorization")
_SENSITIVE_VALUE_HINT_FIELDS = frozenset({"name", "field", "key", "slug", "credential_name", "credential_slug"})
_SECURITY_GUARD = SecurityGuard()


def resolve_webhook_event_hash(payload: Mapping[str, object]) -> str:
    """Build one stable dedupe hash for a webhook delivery payload."""

    event_key = extract_webhook_event_key(payload)
    if event_key is None:
        # No explicit idempotency key: avoid false dedupe across different events
        # with equal payload bodies by using one-time per-request hash.
        return sha256(secrets.token_urlsafe(32).encode("utf-8")).hexdigest()
    return sha256(f"event:{event_key}".encode("utf-8")).hexdigest()


def extract_webhook_event_key(payload: Mapping[str, object]) -> str | None:
    """Extract one delivery id/idempotency key from webhook payload or headers."""

    key_candidates = (
        "event_id",
        "idempotency_key",
        "delivery_id",
        "message_id",
        "request_id",
        "update_id",
    )
    for candidate in key_candidates:
        value = payload.get(candidate)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    headers = payload.get("headers")
    if isinstance(headers, Mapping):
        normalized_headers = {str(key).lower(): value for key, value in headers.items()}
        header_candidates = (
            "x-github-delivery",
            "x-request-id",
            "x-idempotency-key",
            "x-event-id",
        )
        for header_name in header_candidates:
            value = normalized_headers.get(header_name)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return None


def sanitize_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Redact tokens/secrets from payload fields before LLM exposure."""

    return {
        str(key): sanitize_payload_value(value, field_name=str(key))
        for key, value in payload.items()
    }


def sanitize_payload_value(value: object, *, field_name: str | None = None) -> object:
    """Redact a single payload value recursively."""

    if field_name is not None and is_sensitive_field_name(field_name):
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
                else sanitize_payload_value(item, field_name=str(key))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload_value(item) for item in value]
    return repr(value)


def is_sensitive_field_name(field_name: str) -> bool:
    """Return whether a field name should be fully redacted."""

    normalized = field_name.lower()
    return any(part in normalized for part in _SENSITIVE_FIELD_PARTS)


def _mapping_has_sensitive_value_hint(value: dict[object, object]) -> bool:
    for key, item in value.items():
        if str(key).lower() not in _SENSITIVE_VALUE_HINT_FIELDS:
            continue
        if isinstance(item, str) and is_sensitive_field_name(item):
            return True
    return False
