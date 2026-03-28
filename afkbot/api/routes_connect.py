"""Connect REST routes for desktop app pairing flow."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from afkbot.services.connect import (
    ConnectServiceError,
    ConnectClientMetadata,
    claim_connect_token,
    refresh_connect_access_token,
    revoke_connect_session,
)
from afkbot.services.connect.rate_limit import enforce_connect_rate_limit
from afkbot.services.session_ids import MAX_SESSION_ID_LENGTH
from afkbot.settings import get_settings

router = APIRouter(prefix="/v1/connect", tags=["connect"])


class ConnectClientInfo(BaseModel):
    """Optional desktop client fingerprint metadata."""

    model_config = ConfigDict(extra="forbid")

    platform: str | None = Field(default=None, min_length=1, max_length=32)
    app_version: str | None = Field(default=None, min_length=1, max_length=64)


class ConnectClaimRequest(BaseModel):
    """Request payload for one-time connect token claim."""

    model_config = ConfigDict(extra="forbid")

    claim_token: str = Field(min_length=1)
    claim_pin: str | None = Field(default=None, min_length=1, max_length=64)
    client: ConnectClientInfo | None = None


class ConnectClaimResponse(BaseModel):
    """Successful connect-claim response."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    access_token: str
    refresh_token: str
    session_proof: str
    expires_in_sec: int
    base_url: str
    profile_id: str
    session_id: str


class ConnectRefreshRequest(BaseModel):
    """Refresh request payload."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)
    session_proof: str = Field(min_length=1)
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SESSION_ID_LENGTH,
    )


class ConnectRefreshResponse(BaseModel):
    """Successful access-token refresh response."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    access_token: str
    refresh_token: str
    expires_in_sec: int
    session_id: str


class ConnectRevokeRequest(BaseModel):
    """Revoke request payload."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)
    session_proof: str = Field(min_length=1)


class ConnectRevokeResponse(BaseModel):
    """Successful refresh-session revoke response."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True


@router.post("/claim", response_model=ConnectClaimResponse)
async def post_connect_claim(request: ConnectClaimRequest, http_request: Request) -> ConnectClaimResponse:
    """Consume one-time claim token and issue desktop session tokens."""

    try:
        await enforce_connect_rate_limit(
            settings=get_settings(),
            scope="claim",
            remote_host=_remote_host(http_request),
            token_value=request.claim_token,
        )
        result = await claim_connect_token(
            claim_token=request.claim_token,
            claim_pin=request.claim_pin,
            client=_to_client_metadata(request.client),
        )
    except ConnectServiceError as exc:
        raise _to_http_exception(exc) from exc
    return ConnectClaimResponse(
        ok=True,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        session_proof=result.session_proof,
        expires_in_sec=result.expires_in_sec,
        base_url=result.base_url,
        profile_id=result.profile_id,
        session_id=result.session_id,
    )


@router.post("/refresh", response_model=ConnectRefreshResponse)
async def post_connect_refresh(request: ConnectRefreshRequest, http_request: Request) -> ConnectRefreshResponse:
    """Validate refresh token and issue new access token."""

    try:
        await enforce_connect_rate_limit(
            settings=get_settings(),
            scope="refresh",
            remote_host=_remote_host(http_request),
            token_value=request.refresh_token,
        )
        result = await refresh_connect_access_token(
            refresh_token=request.refresh_token,
            session_proof=request.session_proof,
            session_id=request.session_id,
        )
    except ConnectServiceError as exc:
        raise _to_http_exception(exc) from exc
    return ConnectRefreshResponse(
        ok=True,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in_sec=result.expires_in_sec,
        session_id=result.session_id,
    )


@router.post("/revoke", response_model=ConnectRevokeResponse)
async def post_connect_revoke(request: ConnectRevokeRequest, http_request: Request) -> ConnectRevokeResponse:
    """Revoke refresh-session token."""

    try:
        await enforce_connect_rate_limit(
            settings=get_settings(),
            scope="revoke",
            remote_host=_remote_host(http_request),
            token_value=request.refresh_token,
        )
        ok = await revoke_connect_session(
            refresh_token=request.refresh_token,
            session_proof=request.session_proof,
        )
    except ConnectServiceError as exc:
        raise _to_http_exception(exc) from exc
    return ConnectRevokeResponse(ok=ok)


def _to_http_exception(exc: ConnectServiceError) -> HTTPException:
    status_code = _status_code_for_error(exc.error_code)
    headers: dict[str, str] = {}
    if exc.retry_after_sec is not None:
        headers["Retry-After"] = str(exc.retry_after_sec)
    return HTTPException(
        status_code=status_code,
        detail={
            "ok": False,
            "error_code": exc.error_code,
            "reason": exc.reason,
        },
        headers=headers or None,
    )


def _status_code_for_error(error_code: str) -> int:
    if error_code == "connect_token_expired":
        return status.HTTP_410_GONE
    if error_code == "connect_profile_not_found":
        return status.HTTP_404_NOT_FOUND
    if error_code in {"connect_token_used"}:
        return status.HTTP_409_CONFLICT
    if error_code in {"connect_session_profile_mismatch"}:
        return status.HTTP_409_CONFLICT
    if error_code in {"connect_session_override_forbidden"}:
        return status.HTTP_403_FORBIDDEN
    if error_code in {"connect_claim_pin_locked"}:
        return status.HTTP_403_FORBIDDEN
    if error_code in {"connect_base_url_insecure"}:
        return status.HTTP_400_BAD_REQUEST
    if error_code in {"connect_token_invalid", "connect_refresh_token_invalid"}:
        return status.HTTP_401_UNAUTHORIZED
    if error_code in {
        "connect_claim_pin_missing",
        "connect_claim_pin_invalid",
        "connect_refresh_token_revoked",
        "connect_refresh_token_expired",
        "connect_session_proof_missing",
        "connect_session_proof_invalid",
        "connect_session_reauth_required",
    }:
        return status.HTTP_401_UNAUTHORIZED
    if error_code in {"connect_rate_limited"}:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if error_code in {"connect_refresh_issue_failed", "connect_access_issue_failed"}:
        return status.HTTP_500_INTERNAL_SERVER_ERROR
    return status.HTTP_400_BAD_REQUEST


def _to_client_metadata(client: ConnectClientInfo | None) -> ConnectClientMetadata | None:
    """Convert validated API request metadata into service-level connect metadata."""

    if client is None:
        return None
    metadata = ConnectClientMetadata(
        platform=client.platform,
        app_version=client.app_version,
    )
    return metadata if metadata.serialize() else None


def _remote_host(request: Request) -> str | None:
    client = request.client
    if client is None:
        return None
    return client.host
