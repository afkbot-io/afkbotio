"""Service tests for UI auth password hashing helpers."""

from __future__ import annotations

from afkbot.services.ui_auth import hash_ui_auth_password, verify_ui_auth_password


def test_ui_auth_password_hash_roundtrip() -> None:
    """Scrypt UI auth hashes should verify only the original password."""

    encoded = hash_ui_auth_password("correct-horse-battery")

    assert encoded.startswith("scrypt$")
    assert verify_ui_auth_password("correct-horse-battery", encoded) is True
    assert verify_ui_auth_password("wrong-password", encoded) is False

