"""Claim, refresh, and revoke flows for desktop connect tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets

from sqlalchemy.exc import IntegrityError

from afkbot.db.engine import create_engine
from afkbot.repositories.connect_repo import ConnectRepository
from afkbot.services.agent_loop.sessions import SessionProfileMismatchError, SessionService
from afkbot.services.connect.context_snapshot import (
    merge_client_metadata,
    serialize_runtime_metadata,
    snapshot_from_claim_row,
)
from afkbot.services.connect.contracts import (
    ConnectClaimResult,
    ConnectClientMetadata,
    ConnectRefreshResult,
    ConnectServiceError,
)
from afkbot.services.connect.helpers import (
    DEFAULT_ACCESS_TTL_SEC,
    DEFAULT_CONNECT_CLAIM_PIN_MAX_ATTEMPTS,
    DEFAULT_REFRESH_TTL_SEC,
    as_utc,
    hash_token,
    normalize_session_id,
    validate_claim_pin,
    validate_session_proof,
)
from afkbot.services.connect.session_runtime import connect_session_scope
from afkbot.settings import Settings


async def claim_connect_token(
    *,
    settings: Settings,
    claim_token: str,
    claim_pin: str | None = None,
    client: ConnectClientMetadata | None = None,
    access_ttl_sec: int = DEFAULT_ACCESS_TTL_SEC,
    refresh_ttl_sec: int = DEFAULT_REFRESH_TTL_SEC,
) -> ConnectClaimResult:
    """Consume one-time claim token and return desktop session credentials."""

    token = claim_token.strip()
    if not token:
        raise ConnectServiceError(error_code="connect_token_invalid", reason="Claim token is empty.")

    now = datetime.now(tz=UTC)
    claim_hash = hash_token(token)
    access_token = secrets.token_urlsafe(32)
    access_token_hash = hash_token(access_token)
    refresh_token = secrets.token_urlsafe(32)
    refresh_hash = hash_token(refresh_token)
    session_proof = secrets.token_urlsafe(24)
    session_proof_hash = hash_token(session_proof)
    refresh_expires_at = now + timedelta(seconds=max(60, int(refresh_ttl_sec)))
    expires_in_sec = max(60, int(access_ttl_sec))
    access_expires_at = now + timedelta(seconds=expires_in_sec)

    async with connect_session_scope(
        settings=settings,
        engine_factory=create_engine,
    ) as db:
        repo = ConnectRepository(db)
        claim_row = await repo.get_claim_token_by_hash(token_hash=claim_hash)
        if claim_row is None:
            raise ConnectServiceError(
                error_code="connect_token_invalid",
                reason="Connect claim token not found.",
            )
        if claim_row.used_at is not None:
            raise ConnectServiceError(
                error_code="connect_token_used",
                reason="Connect claim token already used.",
            )
        if as_utc(claim_row.expires_at) <= now:
            raise ConnectServiceError(
                error_code="connect_token_expired",
                reason="Connect claim token expired.",
            )
        if claim_row.claim_blocked_at is not None:
            raise ConnectServiceError(
                error_code="connect_claim_pin_locked",
                reason="Connect claim token is blocked after too many invalid PIN attempts.",
            )
        try:
            validate_claim_pin(
                required_hash=claim_row.claim_pin_hash,
                claim_pin=claim_pin,
            )
        except ConnectServiceError as exc:
            if exc.error_code not in {"connect_claim_pin_missing", "connect_claim_pin_invalid"}:
                raise
            failed_attempts = int(getattr(claim_row, "claim_failed_attempts", 0) or 0) + 1
            max_attempts = max(
                int(getattr(settings, "connect_claim_pin_max_attempts", DEFAULT_CONNECT_CLAIM_PIN_MAX_ATTEMPTS)),
                1,
            )
            blocked_at = now if failed_attempts >= max_attempts else None
            await repo.update_claim_pin_failure_state(
                claim_id=claim_row.id,
                failed_attempts=failed_attempts,
                blocked_at=blocked_at,
            )
            # Persist lockout state even though this claim attempt fails and the outer scope raises.
            await db.commit()
            if blocked_at is not None:
                raise ConnectServiceError(
                    error_code="connect_claim_pin_locked",
                    reason="Connect claim token is blocked after too many invalid PIN attempts.",
                ) from exc
            raise
        consumed = await repo.consume_claim_token(claim_id=claim_row.id, used_at=now)
        if not consumed:
            raise ConnectServiceError(
                error_code="connect_token_used",
                reason="Connect claim token already used.",
            )
        context_snapshot = merge_client_metadata(
            snapshot=snapshot_from_claim_row(claim_row),
            client=client,
        )
        runtime_metadata_json = serialize_runtime_metadata(
            None if context_snapshot is None else context_snapshot.runtime_metadata
        )
        prompt_overlay = None if context_snapshot is None else context_snapshot.prompt_overlay

        try:
            refresh_row = await repo.create_refresh_session(
                profile_id=claim_row.profile_id,
                session_id=claim_row.session_id,
                base_url=claim_row.base_url,
                refresh_token_hash=refresh_hash,
                session_proof_hash=session_proof_hash,
                allow_diagnostics=bool(getattr(claim_row, "allow_diagnostics", False)),
                runtime_metadata_json=runtime_metadata_json,
                prompt_overlay=prompt_overlay,
                expires_at=refresh_expires_at,
            )
        except IntegrityError as exc:
            raise ConnectServiceError(
                error_code="connect_refresh_issue_failed",
                reason="Could not issue refresh token.",
            ) from exc

        try:
            await repo.create_access_token(
                profile_id=claim_row.profile_id,
                session_id=claim_row.session_id,
                refresh_session_id=refresh_row.id,
                base_url=claim_row.base_url,
                access_token_hash=access_token_hash,
                allow_diagnostics=bool(getattr(claim_row, "allow_diagnostics", False)),
                runtime_metadata_json=runtime_metadata_json,
                prompt_overlay=prompt_overlay,
                expires_at=access_expires_at,
            )
        except IntegrityError as exc:
            raise ConnectServiceError(
                error_code="connect_access_issue_failed",
                reason="Could not issue access token.",
            ) from exc

        return ConnectClaimResult(
            access_token=access_token,
            refresh_token=refresh_token,
            session_proof=session_proof,
            expires_in_sec=expires_in_sec,
            base_url=claim_row.base_url,
            profile_id=claim_row.profile_id,
            session_id=claim_row.session_id,
        )


async def refresh_connect_access_token(
    *,
    settings: Settings,
    refresh_token: str,
    session_proof: str | None = None,
    session_id: str | None = None,
    access_ttl_sec: int = DEFAULT_ACCESS_TTL_SEC,
) -> ConnectRefreshResult:
    """Validate refresh token and issue a new access token."""

    token = refresh_token.strip()
    if not token:
        raise ConnectServiceError(
            error_code="connect_refresh_token_invalid",
            reason="Refresh token is empty.",
        )

    token_hash = hash_token(token)
    now = datetime.now(tz=UTC)
    expires_in_sec = max(60, int(access_ttl_sec))
    access_token = secrets.token_urlsafe(32)
    access_token_hash = hash_token(access_token)
    new_refresh_token = secrets.token_urlsafe(32)
    new_refresh_hash = hash_token(new_refresh_token)
    access_expires_at = now + timedelta(seconds=expires_in_sec)

    async with connect_session_scope(
        settings=settings,
        engine_factory=create_engine,
    ) as db:
        repo = ConnectRepository(db)
        row = await repo.get_refresh_session_by_hash(refresh_token_hash=token_hash)
        if row is None:
            raise ConnectServiceError(
                error_code="connect_refresh_token_invalid",
                reason="Refresh token not found.",
            )
        if row.revoked_at is not None:
            raise ConnectServiceError(
                error_code="connect_refresh_token_revoked",
                reason="Refresh token is revoked.",
            )
        if as_utc(row.expires_at) <= now:
            raise ConnectServiceError(
                error_code="connect_refresh_token_expired",
                reason="Refresh token is expired.",
            )
        if not (row.session_proof_hash or "").strip():
            raise ConnectServiceError(
                error_code="connect_session_reauth_required",
                reason="Connect session must be paired again to enable proof-bound access.",
            )
        validate_session_proof(
            required_hash=row.session_proof_hash,
            proof_token=session_proof,
        )
        resolved_session_id = normalize_session_id(value=session_id, fallback=row.session_id)
        if resolved_session_id != row.session_id:
            raise ConnectServiceError(
                error_code="connect_session_override_forbidden",
                reason="Refresh token is bound to its original session_id and cannot mint tokens for other sessions.",
            )
        rotated = await repo.rotate_refresh_session_token(
            row_id=row.id,
            current_refresh_token_hash=token_hash,
            new_refresh_token_hash=new_refresh_hash,
        )
        if not rotated:
            raise ConnectServiceError(
                error_code="connect_refresh_token_invalid",
                reason="Refresh token has already been rotated or revoked.",
            )
        try:
            await SessionService(db).get_or_create(
                session_id=resolved_session_id,
                profile_id=row.profile_id,
            )
        except SessionProfileMismatchError as exc:
            raise ConnectServiceError(
                error_code="connect_session_profile_mismatch",
                reason=str(exc),
            ) from exc
        try:
            await repo.create_access_token(
                profile_id=row.profile_id,
                session_id=resolved_session_id,
                refresh_session_id=row.id,
                base_url=row.base_url,
                access_token_hash=access_token_hash,
                allow_diagnostics=bool(getattr(row, "allow_diagnostics", False)),
                runtime_metadata_json=row.runtime_metadata_json,
                prompt_overlay=row.prompt_overlay,
                expires_at=access_expires_at,
            )
        except IntegrityError as exc:
            raise ConnectServiceError(
                error_code="connect_access_issue_failed",
                reason="Could not issue access token.",
            ) from exc

    return ConnectRefreshResult(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in_sec=expires_in_sec,
        session_id=resolved_session_id,
    )


async def revoke_connect_session(
    *,
    settings: Settings,
    refresh_token: str,
    session_proof: str | None = None,
) -> bool:
    """Revoke refresh token session. Idempotent when already revoked."""

    token = refresh_token.strip()
    if not token:
        raise ConnectServiceError(
            error_code="connect_refresh_token_invalid",
            reason="Refresh token is empty.",
        )

    token_hash = hash_token(token)
    now = datetime.now(tz=UTC)

    async with connect_session_scope(
        settings=settings,
        engine_factory=create_engine,
    ) as db:
        repo = ConnectRepository(db)
        row = await repo.get_refresh_session_by_hash(refresh_token_hash=token_hash)
        if row is None:
            raise ConnectServiceError(
                error_code="connect_refresh_token_invalid",
                reason="Refresh token not found.",
            )
        if not (row.session_proof_hash or "").strip():
            raise ConnectServiceError(
                error_code="connect_session_reauth_required",
                reason="Connect session must be paired again to enable proof-bound access.",
            )
        validate_session_proof(
            required_hash=row.session_proof_hash,
            proof_token=session_proof,
        )
        if row.revoked_at is not None:
            await repo.revoke_access_tokens_by_refresh_session(
                refresh_session_id=row.id,
                revoked_at=now,
            )
            return True
        revoked = await repo.revoke_refresh_session(row_id=row.id, revoked_at=now)
        await repo.revoke_access_tokens_by_refresh_session(
            refresh_session_id=row.id,
            revoked_at=now,
        )
        return revoked
