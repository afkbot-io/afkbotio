"""Webhook payload sanitization and idempotency helpers."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from hashlib import sha256

_TOKEN_LIKE_RE = re.compile(r"\b[A-Za-z0-9_\-]{16,}\b")
_SENSITIVE_FIELD_PARTS = ("secret", "token", "password", "api_key", "authorization", "value")


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
        return _TOKEN_LIKE_RE.sub("[REDACTED]", value)
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {
            str(key): sanitize_payload_value(item, field_name=str(key))
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
