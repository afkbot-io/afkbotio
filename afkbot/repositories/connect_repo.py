"""Repository for desktop connect token lifecycle."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.connect_access_token import ConnectAccessToken
from afkbot.models.connect_claim_token import ConnectClaimToken
from afkbot.models.connect_session_token import ConnectSessionToken


class ConnectRepository:
    """Persistence operations for connect claim and refresh-session tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_claim_token(
        self,
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        token_hash: str,
        claim_pin_hash: str | None,
        allow_diagnostics: bool,
        runtime_metadata_json: str | None,
        prompt_overlay: str | None,
        expires_at: datetime,
    ) -> ConnectClaimToken:
        """Create one claim token row."""

        row = ConnectClaimToken(
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
            token_hash=token_hash,
            claim_pin_hash=claim_pin_hash,
            allow_diagnostics=allow_diagnostics,
            runtime_metadata_json=runtime_metadata_json,
            prompt_overlay=prompt_overlay,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_claim_token_by_hash(self, *, token_hash: str) -> ConnectClaimToken | None:
        """Load one claim token row by hash."""

        statement: Select[tuple[ConnectClaimToken]] = select(ConnectClaimToken).where(
            ConnectClaimToken.token_hash == token_hash
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def consume_claim_token(self, *, claim_id: int, used_at: datetime) -> bool:
        """Consume one claim token atomically (single-use)."""

        statement = (
            update(ConnectClaimToken)
            .where(
                ConnectClaimToken.id == claim_id,
                ConnectClaimToken.used_at.is_(None),
            )
            .values(used_at=used_at)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        rowcount = int(getattr(result, "rowcount", 0) or 0)
        return rowcount > 0

    async def update_claim_pin_failure_state(
        self,
        *,
        claim_id: int,
        failed_attempts: int,
        blocked_at: datetime | None,
    ) -> bool:
        """Persist current claim PIN failure state for one token."""

        statement = (
            update(ConnectClaimToken)
            .where(
                ConnectClaimToken.id == claim_id,
                ConnectClaimToken.used_at.is_(None),
            )
            .values(
                claim_failed_attempts=failed_attempts,
                claim_blocked_at=blocked_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def create_refresh_session(
        self,
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        refresh_token_hash: str,
        session_proof_hash: str | None,
        allow_diagnostics: bool,
        runtime_metadata_json: str | None,
        prompt_overlay: str | None,
        expires_at: datetime,
    ) -> ConnectSessionToken:
        """Create one refresh-session row."""

        row = ConnectSessionToken(
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
            refresh_token_hash=refresh_token_hash,
            session_proof_hash=session_proof_hash,
            allow_diagnostics=allow_diagnostics,
            runtime_metadata_json=runtime_metadata_json,
            prompt_overlay=prompt_overlay,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def create_access_token(
        self,
        *,
        profile_id: str,
        session_id: str,
        refresh_session_id: int,
        base_url: str,
        access_token_hash: str,
        allow_diagnostics: bool,
        runtime_metadata_json: str | None,
        prompt_overlay: str | None,
        expires_at: datetime,
    ) -> ConnectAccessToken:
        """Create one access-token row bound to refresh session."""

        row = ConnectAccessToken(
            profile_id=profile_id,
            session_id=session_id,
            refresh_session_id=refresh_session_id,
            base_url=base_url,
            access_token_hash=access_token_hash,
            allow_diagnostics=allow_diagnostics,
            runtime_metadata_json=runtime_metadata_json,
            prompt_overlay=prompt_overlay,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_access_token_by_hash(
        self,
        *,
        access_token_hash: str,
    ) -> ConnectAccessToken | None:
        """Load one access-token row by hash."""

        statement: Select[tuple[ConnectAccessToken]] = select(ConnectAccessToken).where(
            ConnectAccessToken.access_token_hash == access_token_hash
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_refresh_session_by_hash(
        self,
        *,
        refresh_token_hash: str,
    ) -> ConnectSessionToken | None:
        """Load one refresh-session row by refresh hash."""

        statement: Select[tuple[ConnectSessionToken]] = select(ConnectSessionToken).where(
            ConnectSessionToken.refresh_token_hash == refresh_token_hash
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_refresh_session_by_id(
        self,
        *,
        refresh_session_id: int,
    ) -> ConnectSessionToken | None:
        """Load one refresh-session row by primary key."""

        statement: Select[tuple[ConnectSessionToken]] = select(ConnectSessionToken).where(
            ConnectSessionToken.id == refresh_session_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def revoke_access_tokens_by_refresh_session(
        self,
        *,
        refresh_session_id: int,
        revoked_at: datetime,
    ) -> int:
        """Revoke active access tokens for one refresh session."""

        statement = (
            update(ConnectAccessToken)
            .where(
                ConnectAccessToken.refresh_session_id == refresh_session_id,
                ConnectAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def revoke_refresh_session(self, *, row_id: int, revoked_at: datetime) -> bool:
        """Revoke active refresh-session by row id."""

        statement = (
            update(ConnectSessionToken)
            .where(
                ConnectSessionToken.id == row_id,
                ConnectSessionToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        rowcount = int(getattr(result, "rowcount", 0) or 0)
        return rowcount > 0

    async def rotate_refresh_session_token(
        self,
        *,
        row_id: int,
        current_refresh_token_hash: str,
        new_refresh_token_hash: str,
    ) -> bool:
        """Atomically replace refresh token hash for one live refresh session."""

        statement = (
            update(ConnectSessionToken)
            .where(
                ConnectSessionToken.id == row_id,
                ConnectSessionToken.revoked_at.is_(None),
                ConnectSessionToken.refresh_token_hash == current_refresh_token_hash,
            )
            .values(refresh_token_hash=new_refresh_token_hash)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0) > 0
