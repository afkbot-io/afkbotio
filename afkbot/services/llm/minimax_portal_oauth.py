"""MiniMax Portal OAuth region and token helpers."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Literal


MiniMaxRegion = Literal["global", "cn"]
MINIMAX_PORTAL_REGION_GLOBAL: MiniMaxRegion = "global"
MINIMAX_PORTAL_REGION_CN: MiniMaxRegion = "cn"
MINIMAX_PORTAL_REGION_CHOICES: tuple[MiniMaxRegion, MiniMaxRegion] = (
    MINIMAX_PORTAL_REGION_GLOBAL,
    MINIMAX_PORTAL_REGION_CN,
)
MINIMAX_PORTAL_OAUTH_CLIENT_ID = "78257093-7e40-4613-99e0-527b14b39113"
MINIMAX_PORTAL_OAUTH_BASE_URL_GLOBAL = "https://api.minimax.io"
MINIMAX_PORTAL_OAUTH_BASE_URL_CN = "https://api.minimaxi.com"
MINIMAX_PORTAL_PROVIDER_BASE_URL_GLOBAL = "https://api.minimax.io/v1"
MINIMAX_PORTAL_PROVIDER_BASE_URL_CN = "https://api.minimaxi.com/v1"


@dataclass(frozen=True, slots=True)
class MiniMaxPortalOAuthToken:
    """Normalized MiniMax OAuth token payload."""

    access_token: str
    refresh_token: str
    expires_at_epoch_sec: int
    resource_url: str | None = None


def normalize_minimax_portal_region(
    value: str | None,
    *,
    default: MiniMaxRegion = MINIMAX_PORTAL_REGION_GLOBAL,
) -> MiniMaxRegion:
    """Normalize optional MiniMax region value with strict fallback."""

    normalized = (value or "").strip().lower()
    if normalized in MINIMAX_PORTAL_REGION_CHOICES:
        return normalized  # type: ignore[return-value]
    return default


def minimax_portal_oauth_base_url_for_region(region: MiniMaxRegion) -> str:
    """Return OAuth base URL for MiniMax region."""

    if region == MINIMAX_PORTAL_REGION_CN:
        return MINIMAX_PORTAL_OAUTH_BASE_URL_CN
    return MINIMAX_PORTAL_OAUTH_BASE_URL_GLOBAL


def minimax_portal_provider_base_url_for_region(region: MiniMaxRegion) -> str:
    """Return OpenAI-compatible provider base URL for MiniMax region."""

    if region == MINIMAX_PORTAL_REGION_CN:
        return MINIMAX_PORTAL_PROVIDER_BASE_URL_CN
    return MINIMAX_PORTAL_PROVIDER_BASE_URL_GLOBAL


def infer_minimax_portal_region_from_base_url(
    base_url: str | None,
    *,
    default: MiniMaxRegion = MINIMAX_PORTAL_REGION_GLOBAL,
) -> MiniMaxRegion:
    """Infer MiniMax region from one configured base URL."""

    normalized = (base_url or "").strip().lower()
    if "minimaxi.com" in normalized:
        return MINIMAX_PORTAL_REGION_CN
    if "minimax.io" in normalized:
        return MINIMAX_PORTAL_REGION_GLOBAL
    return default


def normalize_minimax_portal_token_expiry(
    raw_expiry: object,
    *,
    now_epoch_sec: int | None = None,
) -> int:
    """Normalize MiniMax token expiry into epoch seconds.

    Supports epoch milliseconds, epoch seconds, and TTL seconds.
    """

    now = now_epoch_sec if now_epoch_sec is not None else int(time.time())
    if isinstance(raw_expiry, int | float):
        value = float(raw_expiry)
    elif isinstance(raw_expiry, str) and raw_expiry.strip():
        value = float(raw_expiry.strip())
    else:
        return now + 3600

    if value > 100_000_000_000:
        return int(value / 1000.0)
    if value > 1_000_000_000:
        return int(value)
    return now + max(1, int(value))


def normalize_minimax_portal_resource_url(raw_value: object) -> str | None:
    """Normalize optional MiniMax resource URL into non-empty string."""

    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip()
    return normalized or None


def extract_minimax_oauth_error_message(payload: object) -> str | None:
    """Extract best-effort readable MiniMax OAuth error message."""

    if not isinstance(payload, dict):
        return None
    base_resp = payload.get("base_resp")
    if isinstance(base_resp, dict):
        status_msg = str(base_resp.get("status_msg") or "").strip()
        if status_msg:
            return status_msg
    message = str(payload.get("message") or payload.get("error_description") or "").strip()
    if message:
        return message
    error = str(payload.get("error") or "").strip()
    if error:
        return error
    return None


def parse_minimax_portal_token_payload(
    payload: object,
    *,
    default_refresh_token: str | None = None,
    now_epoch_sec: int | None = None,
) -> MiniMaxPortalOAuthToken:
    """Parse one MiniMax OAuth token payload into normalized shape."""

    if not isinstance(payload, dict):
        raise ValueError("MiniMax OAuth token payload is invalid.")
    status = str(payload.get("status") or "").strip().lower()
    if status and status != "success":
        message = extract_minimax_oauth_error_message(payload) or "MiniMax OAuth request failed."
        raise ValueError(message)
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("MiniMax OAuth token payload is missing access_token.")
    refresh_token = str(payload.get("refresh_token") or "").strip() or (default_refresh_token or "").strip()
    if not refresh_token:
        raise ValueError("MiniMax OAuth token payload is missing refresh_token.")
    expires_at = normalize_minimax_portal_token_expiry(
        payload.get("expired_in") or payload.get("expires_in") or payload.get("expires_at"),
        now_epoch_sec=now_epoch_sec,
    )
    return MiniMaxPortalOAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at_epoch_sec=expires_at,
        resource_url=normalize_minimax_portal_resource_url(
            payload.get("resource_url") or payload.get("resourceUrl")
        ),
    )
