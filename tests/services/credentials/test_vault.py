"""Tests for credentials vault encryption/decryption."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from afkbot.services.credentials.vault import CredentialsVault, CredentialsVaultUnavailableError


def test_vault_encrypt_decrypt_roundtrip() -> None:
    """Vault should decrypt values encrypted with active key."""

    key = Fernet.generate_key().decode("utf-8")
    vault = CredentialsVault(key)

    encrypted, key_version = vault.encrypt("super-secret")

    assert encrypted != "super-secret"
    assert key_version.startswith("sha256:")
    assert vault.decrypt(encrypted) == "super-secret"


def test_vault_missing_keys_raises_unavailable() -> None:
    """Vault should fail deterministically when master keys are absent."""

    with pytest.raises(CredentialsVaultUnavailableError, match="Credentials vault is unavailable"):
        CredentialsVault(None)
