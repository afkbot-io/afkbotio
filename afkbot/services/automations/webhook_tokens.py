"""Webhook token hashing and presentation helpers."""

from __future__ import annotations

from hashlib import sha256
from sqlalchemy.exc import IntegrityError

from afkbot.services.automations.contracts import WEBHOOK_INGRESS_PATH


def hash_webhook_token(token: str) -> str:
    """Hash one plaintext webhook token for storage/lookup."""

    return sha256(token.encode("utf-8")).hexdigest()


def mask_webhook_token(token: str | None) -> str:
    """Return one stable masked representation for webhook token output."""

    if not token:
        return "[HIDDEN]"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def build_webhook_path(token: str | None) -> str | None:
    """Build public webhook ingress path (token must be sent in headers)."""

    if token is None:
        return None
    normalized = token.strip()
    if not normalized:
        return None
    return WEBHOOK_INGRESS_PATH


def is_webhook_token_conflict(exc: IntegrityError) -> bool:
    """Return whether one database integrity error is a webhook token collision."""

    message = str(exc.orig if exc.orig is not None else exc).lower()
    return "webhook_token_hash" in message or "ix_automation_webhook_token_hash" in message
