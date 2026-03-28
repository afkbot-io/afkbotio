"""Small pure helpers for connect token lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from ipaddress import ip_address
import secrets
from urllib.parse import urlencode, urlparse, urlunparse

from afkbot.services.connect.contracts import ConnectServiceError
from afkbot.services.session_ids import MAX_SESSION_ID_LENGTH

MIN_CONNECT_TTL_SEC = 30
MAX_CONNECT_TTL_SEC = 3600
DEFAULT_CONNECT_TTL_SEC = 120
DEFAULT_ACCESS_TTL_SEC = 3600
DEFAULT_REFRESH_TTL_SEC = 30 * 24 * 3600
CONNECT_URL_VERSION = "1"
DEFAULT_CONNECT_CLAIM_PIN_MAX_ATTEMPTS = 5


def normalize_base_url(value: str) -> str:
    """Normalize host/base URL into canonical API base URL."""

    raw = value.strip()
    if not raw:
        raise ConnectServiceError(error_code="connect_base_url_invalid", reason="Base URL is empty.")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectServiceError(
            error_code="connect_base_url_invalid",
            reason="Base URL scheme must be http or https.",
        )
    if not parsed.netloc:
        raise ConnectServiceError(
            error_code="connect_base_url_invalid",
            reason="Base URL host is missing.",
        )
    if parsed.scheme == "http" and not _is_local_http_host(parsed.hostname):
        raise ConnectServiceError(
            error_code="connect_base_url_insecure",
            reason="Public connect base URL must use https.",
        )
    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )
    return normalized.rstrip("/")


def is_loopback_base_url(value: str) -> bool:
    """Return true when the base URL resolves to a same-device loopback host."""

    parsed = urlparse(value.strip())
    return _is_loopback_host(parsed.hostname)


def normalize_ttl(value: int | None) -> int:
    """Clamp connect claim TTL into supported bounds."""

    ttl = DEFAULT_CONNECT_TTL_SEC if value is None else int(value)
    if ttl < MIN_CONNECT_TTL_SEC:
        return MIN_CONNECT_TTL_SEC
    if ttl > MAX_CONNECT_TTL_SEC:
        return MAX_CONNECT_TTL_SEC
    return ttl


def normalize_session_id(*, value: str | None, fallback: str) -> str:
    """Resolve optional session id override with stable fallback."""

    normalized = str(value or "").strip()
    resolved = normalized or fallback
    if len(resolved) > MAX_SESSION_ID_LENGTH:
        raise ConnectServiceError(
            error_code="connect_session_invalid",
            reason=(
                "Session id is too long. "
                f"Maximum supported length is {MAX_SESSION_ID_LENGTH} characters."
            ),
        )
    return resolved


def normalize_claim_pin(value: str | None) -> str | None:
    """Normalize optional pairing PIN/secret used during connect claim."""

    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) < 4:
        raise ConnectServiceError(
            error_code="connect_claim_pin_invalid",
            reason="Claim PIN must be at least 4 characters.",
        )
    if len(normalized) > 64:
        raise ConnectServiceError(
            error_code="connect_claim_pin_invalid",
            reason="Claim PIN must be at most 64 characters.",
        )
    return normalized


def generate_claim_pin(*, digits: int = 6) -> str:
    """Generate a short numeric out-of-band pairing PIN."""

    size = max(4, int(digits))
    alphabet = "0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(size))


def hash_token(token: str) -> str:
    """Hash plaintext tokens before they are stored in DB."""

    return sha256(token.encode("utf-8")).hexdigest()


def validate_session_proof(*, required_hash: str | None, proof_token: str | None) -> None:
    """Require matching session proof token when a connect session is proof-bound."""

    if not required_hash:
        return
    normalized = (proof_token or "").strip()
    if not normalized:
        raise ConnectServiceError(
            error_code="connect_session_proof_missing",
            reason="Session proof token is required.",
        )
    if hash_token(normalized) != required_hash:
        raise ConnectServiceError(
            error_code="connect_session_proof_invalid",
            reason="Session proof token is invalid.",
        )


def validate_claim_pin(*, required_hash: str | None, claim_pin: str | None) -> None:
    """Require matching claim PIN when a connect claim token is PIN-protected."""

    if not required_hash:
        return
    normalized = normalize_claim_pin(claim_pin)
    if normalized is None:
        raise ConnectServiceError(
            error_code="connect_claim_pin_missing",
            reason="Claim PIN is required.",
        )
    if hash_token(normalized) != required_hash:
        raise ConnectServiceError(
            error_code="connect_claim_pin_invalid",
            reason="Claim PIN is invalid.",
        )


def build_connect_url(
    *,
    base_url: str,
    claim_token: str,
    profile_id: str,
    session_id: str,
    expires_at: datetime,
) -> str:
    """Build deep-link URL consumed by desktop app."""

    query = urlencode(
        {
            "base_url": base_url,
            "claim_token": claim_token,
            "profile_id": profile_id,
            "session_id": session_id,
            "expires_at": format_utc_iso(expires_at),
            "v": CONNECT_URL_VERSION,
        },
        safe=":/",
    )
    return f"afk://connect?{query}"


def format_utc_iso(value: datetime) -> str:
    """Render timestamp as stable UTC ISO string without micros."""

    normalized = value.astimezone(UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def as_utc(value: datetime) -> datetime:
    """Normalize persisted timestamps to UTC-aware datetimes."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_local_http_host(hostname: str | None) -> bool:
    normalized = str(hostname or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    if _is_loopback_host(normalized):
        return True
    if normalized.endswith(".local") or normalized.endswith(".internal"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _is_loopback_host(hostname: str | None) -> bool:
    normalized = str(hostname or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False
