"""Credentials vault backed by MultiFernet encryption keys."""

from __future__ import annotations

from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CredentialsVaultError(Exception):
    """Base vault exception exposing deterministic application error code."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class CredentialsVaultUnavailableError(CredentialsVaultError):
    """Raised when vault keys are missing or invalid."""

    def __init__(self, reason: str = "Credentials vault is unavailable") -> None:
        super().__init__(error_code="credentials_vault_unavailable", reason=reason)


class CredentialsVault:
    """Encrypt and decrypt secrets using rotating MultiFernet keys."""

    def __init__(self, master_keys_csv: str | None) -> None:
        keys = [item.strip() for item in (master_keys_csv or "").split(",") if item.strip()]
        if not keys:
            raise CredentialsVaultUnavailableError()

        try:
            fernets = [Fernet(key.encode("utf-8")) for key in keys]
        except (TypeError, ValueError) as exc:  # invalid key format
            raise CredentialsVaultUnavailableError() from exc

        self._fernet = MultiFernet(fernets)
        self._key_version = f"sha256:{sha256(keys[0].encode('utf-8')).hexdigest()[:12]}"

    @property
    def key_version(self) -> str:
        """Return deterministic version marker for active encryption key."""

        return self._key_version

    def encrypt(self, plaintext: str) -> tuple[str, str]:
        """Encrypt plaintext and return ciphertext plus key version metadata."""

        try:
            token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            raise CredentialsVaultUnavailableError() from exc
        return token, self._key_version

    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt ciphertext and return plaintext without leaking input in errors."""

        try:
            value = self._fernet.decrypt(encrypted_value.encode("utf-8"))
            return value.decode("utf-8")
        except InvalidToken as exc:
            raise CredentialsVaultUnavailableError() from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise CredentialsVaultUnavailableError() from exc
