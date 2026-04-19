"""Password hashing helpers for AFKBOT UI auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


_SCRYPT_PREFIX = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_MIN_PASSWORD_LENGTH = 8


def hash_ui_auth_password(password: str) -> str:
    """Hash a UI auth password with scrypt and a random salt."""

    normalized = str(password)
    if len(normalized) < _MIN_PASSWORD_LENGTH:
        raise ValueError(f"UI auth password must be at least {_MIN_PASSWORD_LENGTH} characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        normalized.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        (
            _SCRYPT_PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            str(_SCRYPT_DKLEN),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_ui_auth_password(password: str, encoded_hash: str | None) -> bool:
    """Verify a password against the stored UI auth scrypt hash."""

    normalized = str(password)
    if not encoded_hash:
        return False
    params = _parse_scrypt_hash(encoded_hash)
    if params is None:
        return False
    n, r, p, dklen, salt, expected = params
    actual = hashlib.scrypt(
        normalized.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
    )
    return hmac.compare_digest(actual, expected)


def password_hash_fingerprint(encoded_hash: str | None) -> str:
    """Return a short deterministic fingerprint for session invalidation."""

    normalized = str(encoded_hash or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _parse_scrypt_hash(encoded_hash: str) -> tuple[int, int, int, int, bytes, bytes] | None:
    parts = encoded_hash.split("$")
    if len(parts) != 7 or parts[0] != _SCRYPT_PREFIX:
        return None
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        dklen = int(parts[4])
        salt = base64.urlsafe_b64decode(parts[5].encode("ascii"))
        expected = base64.urlsafe_b64decode(parts[6].encode("ascii"))
    except (ValueError, TypeError):
        return None
    if n < 2 or r < 1 or p < 1 or dklen < 16:
        return None
    return n, r, p, dklen, salt, expected

