"""Encryption helpers for durable webhook token reveal paths."""

from __future__ import annotations

from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook
from afkbot.services.credentials.vault import CredentialsVault, CredentialsVaultUnavailableError
from afkbot.settings import Settings


def encrypt_webhook_token(
    *,
    plaintext_token: str,
    settings: Settings | None,
) -> tuple[str | None, str | None]:
    """Return encrypted token storage payload when the credentials vault is available."""

    vault = _build_vault(settings)
    if vault is None:
        return None, None
    try:
        return vault.encrypt(plaintext_token)
    except CredentialsVaultUnavailableError:
        return None, None


def recover_webhook_token(
    *,
    webhook: AutomationTriggerWebhook | None,
    settings: Settings | None,
) -> str | None:
    """Recover plaintext token from encrypted storage without failing the caller."""

    if webhook is None:
        return None
    encrypted_token = (webhook.encrypted_webhook_token or "").strip()
    if not encrypted_token:
        return None
    vault = _build_vault(settings)
    if vault is None:
        return None
    try:
        plaintext = vault.decrypt(encrypted_token)
    except CredentialsVaultUnavailableError:
        return None
    normalized = plaintext.strip()
    return normalized or None


def _build_vault(settings: Settings | None) -> CredentialsVault | None:
    """Return a vault instance or None when encryption is not configured."""

    master_keys = None if settings is None else settings.credentials_master_keys
    try:
        return CredentialsVault(master_keys)
    except CredentialsVaultUnavailableError:
        return None
