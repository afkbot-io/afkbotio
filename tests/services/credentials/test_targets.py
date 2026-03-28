"""Tests for credential target name normalization helpers."""

from __future__ import annotations

import pytest

from afkbot.services.credentials.errors import CredentialsServiceError
from afkbot.services.credentials.targets import normalize_profile_key, validate_credential_name


def test_validate_credential_name_accepts_expected_safe_characters() -> None:
    """Credential names should allow alnum plus dot, underscore, and hyphen."""

    validate_credential_name("smtp-password_01.token")


@pytest.mark.parametrize("raw_name", ["bad\\name", "bad/name", ""])
def test_validate_credential_name_rejects_invalid_characters(raw_name: str) -> None:
    """Credential names should reject backslashes and other unsupported separators."""

    with pytest.raises(CredentialsServiceError, match="Invalid credential name"):
        validate_credential_name(raw_name)


def test_normalize_profile_key_rejects_backslash() -> None:
    """Credential profile keys should not accept Windows-style path separators."""

    with pytest.raises(CredentialsServiceError, match="Invalid credential profile key"):
        normalize_profile_key(r"team\ops")
