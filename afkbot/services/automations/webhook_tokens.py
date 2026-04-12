"""Webhook token hashing and presentation helpers."""

from __future__ import annotations

import secrets
from hashlib import sha256
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError

from afkbot.services.automations.contracts import WEBHOOK_INGRESS_PATH

_WEBHOOK_TOKEN_REF_PREFIX = "sha256:"


def issue_webhook_token() -> str:
    """Issue one new webhook token suitable for path-based delivery."""

    return secrets.token_urlsafe(24)


def hash_webhook_token(token: str) -> str:
    """Hash one plaintext webhook token for storage/lookup."""

    return sha256(token.encode("utf-8")).hexdigest()


def stored_webhook_token_ref(token_hash: str) -> str:
    """Build a non-bearer value for the legacy plaintext-token column."""

    normalized = token_hash.strip()
    return f"{_WEBHOOK_TOKEN_REF_PREFIX}{normalized}"


def stored_webhook_token_ref_hash(value: str | None) -> str | None:
    """Extract a hash from one stored legacy-column token reference."""

    normalized = (value or "").strip()
    if not normalized.startswith(_WEBHOOK_TOKEN_REF_PREFIX):
        return None
    token_hash = normalized.removeprefix(_WEBHOOK_TOKEN_REF_PREFIX).strip()
    return token_hash or None


def mask_webhook_token(token: str | None) -> str:
    """Return one stable masked representation for webhook token output."""

    if not token:
        return "[HIDDEN]"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def build_webhook_path(profile_id: str, token: str | None) -> str | None:
    """Build public webhook ingress path with embedded profile id and token."""

    if token is None:
        return None
    normalized_profile = profile_id.strip()
    normalized = token.strip()
    if not normalized_profile or not normalized:
        return None
    quoted_profile = quote(normalized_profile, safe="")
    quoted_token = quote(normalized, safe="")
    return f"{WEBHOOK_INGRESS_PATH}/{quoted_profile}/webhook/{quoted_token}"


def build_webhook_url(base_url: str | None, profile_id: str, token: str | None) -> str | None:
    """Build one absolute webhook URL when a runtime base URL is available."""

    path = build_webhook_path(profile_id, token)
    if path is None:
        return None
    normalized_base = (base_url or "").strip().rstrip("/")
    if not normalized_base:
        return None
    return f"{normalized_base}{path}"


def is_webhook_token_conflict(exc: IntegrityError) -> bool:
    """Return whether one database integrity error is a webhook token collision."""

    message = str(exc.orig if exc.orig is not None else exc).lower()
    return (
        "webhook_token" in message
        or "ix_automation_webhook_token" in message
        or "webhook_token_hash" in message
        or "ix_automation_webhook_token_hash" in message
    )
