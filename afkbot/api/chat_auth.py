"""Auth and scope helpers shared by chat HTTP and WebSocket adapters."""

from __future__ import annotations

from fastapi import HTTPException, status

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect import (
    ConnectAccessTokenContext,
    ConnectServiceError,
    validate_connect_access_token,
)
from afkbot.services.connect.context_snapshot import snapshot_from_token_context
from afkbot.services.agent_loop.api_runtime import get_api_session_factory


async def require_chat_http_context(
    *,
    authorization: str | None,
    session_proof: str | None = None,
) -> ConnectAccessTokenContext:
    """Validate bearer token for HTTP chat routes or raise deterministic auth error."""

    context, auth_error = await _validate_chat_context_or_error(
        authorization=authorization,
        session_proof=session_proof,
    )
    if auth_error is not None:
        raise _chat_auth_http_error(
            status_code=_http_status_for_auth_error(auth_error),
            error_code=str(auth_error["error_code"]),
            reason=str(auth_error["reason"]),
        )
    assert context is not None
    return context


def ensure_chat_target_scope(
    *,
    context: ConnectAccessTokenContext,
    profile_id: str,
    session_id: str,
) -> None:
    """Reject chat access when token scope mismatches target profile or session."""

    if context.profile_id != profile_id:
        raise _chat_auth_http_error(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="chat_access_scope_mismatch",
            reason=_profile_scope_mismatch_reason(
                token_profile_id=context.profile_id,
                request_profile_id=profile_id,
            ),
        )
    if context.session_id != session_id:
        raise _chat_auth_http_error(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="chat_access_scope_mismatch",
            reason=_session_scope_mismatch_reason(
                token_session_id=context.session_id,
                request_session_id=session_id,
            ),
        )


def build_chat_auth_runtime_overrides(
    context: ConnectAccessTokenContext,
) -> TurnContextOverrides | None:
    """Convert validated access-token routing snapshot into trusted turn overrides."""

    snapshot = snapshot_from_token_context(context)
    if snapshot is None:
        return None
    return snapshot.to_turn_context_overrides()


def has_chat_auth_routing_snapshot(context: ConnectAccessTokenContext) -> bool:
    """Return whether validated access token carries persisted routing context."""

    snapshot = snapshot_from_token_context(context)
    return snapshot is not None


async def build_ws_auth_context_or_error(
    *,
    authorization: str | None,
    session_proof: str | None = None,
) -> tuple[ConnectAccessTokenContext | None, dict[str, object] | None]:
    """Validate WS bearer token and return either auth context or deterministic error payload."""

    return await _validate_chat_context_or_error(
        authorization=authorization,
        session_proof=session_proof,
    )


async def revalidate_ws_active_scope_context_or_error(
    *,
    authorization: str | None,
    session_proof: str | None,
    profile_id: str,
    session_id: str,
) -> tuple[ConnectAccessTokenContext | None, dict[str, object] | None]:
    """Revalidate WS auth and scope, returning a refreshed auth context on success."""

    context, auth_error = await build_ws_auth_context_or_error(
        authorization=authorization,
        session_proof=session_proof,
    )
    if auth_error is not None:
        return None, auth_error
    assert context is not None
    scope_error = build_ws_scope_error(
        context=context,
        profile_id=profile_id,
        session_id=session_id,
    )
    if scope_error is not None:
        return None, scope_error
    return context, None


def build_ws_scope_error(
    *,
    context: ConnectAccessTokenContext,
    profile_id: str,
    session_id: str,
) -> dict[str, object] | None:
    """Build deterministic WS payload for token scope mismatch."""

    if context.profile_id != profile_id:
        return _chat_auth_payload(
            error_code="chat_access_scope_mismatch",
            reason=_profile_scope_mismatch_reason(
                token_profile_id=context.profile_id,
                request_profile_id=profile_id,
            ),
        )
    if context.session_id != session_id:
        return _chat_auth_payload(
            error_code="chat_access_scope_mismatch",
            reason=_session_scope_mismatch_reason(
                token_session_id=context.session_id,
                request_session_id=session_id,
            ),
        )
    return None


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    prefix, separator, token = authorization.partition(" ")
    if separator != " ":
        return None
    if prefix.strip().lower() != "bearer":
        return None
    normalized = token.strip()
    if not normalized:
        return None
    return normalized


def _extract_session_proof(session_proof: str | None) -> str | None:
    normalized = (session_proof or "").strip()
    return normalized or None


async def _validate_chat_context_or_error(
    *,
    authorization: str | None,
    session_proof: str | None,
) -> tuple[ConnectAccessTokenContext | None, dict[str, object] | None]:
    """Validate bearer credentials and return either auth context or one stable error payload."""

    token = _extract_bearer_token(authorization)
    if token is None:
        return None, _chat_auth_payload(
            error_code="chat_access_token_missing",
            reason="Authorization header with Bearer token is required.",
        )
    try:
        context = await validate_connect_access_token(
            access_token=token,
            session_proof=_extract_session_proof(session_proof),
            session_factory=get_api_session_factory(),
        )
    except ConnectServiceError as exc:
        return None, _chat_auth_payload(
            error_code=exc.error_code,
            reason=exc.reason,
        )
    return context, None


def _http_status_for_auth_error(auth_error: dict[str, object]) -> int:
    """Map one deterministic auth payload back into the correct HTTP status code."""

    error_code = str(auth_error.get("error_code") or "").strip()
    if error_code == "chat_access_token_missing":
        return status.HTTP_401_UNAUTHORIZED
    return status_code_for_connect_access_error(error_code)


def status_code_for_connect_access_error(error_code: str) -> int:
    if error_code in {
        "connect_access_token_invalid",
        "connect_access_token_expired",
        "connect_access_token_revoked",
        "connect_session_proof_missing",
        "connect_session_proof_invalid",
        "connect_session_reauth_required",
    }:
        return status.HTTP_401_UNAUTHORIZED
    return status.HTTP_400_BAD_REQUEST


def _chat_auth_payload(*, error_code: str, reason: str) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": error_code,
        "reason": reason,
    }


def ws_close_reason(auth_error: dict[str, object]) -> str:
    """Return RFC-safe short close reason for websocket auth failures."""

    error_code = str(auth_error.get("error_code") or "chat_access_denied").strip()
    if not error_code:
        error_code = "chat_access_denied"
    return error_code[:123]


def ws_close_code_for_error(auth_error: dict[str, object]) -> int:
    """Map deterministic WS auth payload into a protocol close code."""

    if auth_error.get("error_code") == "chat_access_scope_mismatch":
        return 4403
    return 4401


def _chat_auth_http_error(*, status_code: int, error_code: str, reason: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=_chat_auth_payload(error_code=error_code, reason=reason),
        headers={"WWW-Authenticate": "Bearer"},
    )


def ensure_health_diagnostics_scope(context: ConnectAccessTokenContext) -> None:
    """Reject operator diagnostics when token does not explicitly allow them."""

    if context.allow_diagnostics:
        return
    raise _chat_auth_http_error(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code="connect_diagnostics_scope_required",
        reason="Access token is not allowed to read operator diagnostics.",
    )


def _profile_scope_mismatch_reason(
    *,
    token_profile_id: str,
    request_profile_id: str,
) -> str:
    return (
        "Access token profile mismatch: "
        f"token(profile_id='{token_profile_id}') "
        f"!= request(profile_id='{request_profile_id}')."
    )


def _session_scope_mismatch_reason(
    *,
    token_session_id: str,
    request_session_id: str,
) -> str:
    return (
        "Access token session mismatch: "
        f"token(session_id='{token_session_id}') "
        f"!= request(session_id='{request_session_id}')."
    )
