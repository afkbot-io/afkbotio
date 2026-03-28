"""Repository for pending secure field requests."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.pending_secure_request import PendingSecureRequest


class PendingSecureRequestRepository:
    """Persistence operations for pending secure requests."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        question_id: str,
        secure_field: str,
        integration_name: str,
        credential_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        nonce: str,
        expires_at: datetime,
    ) -> PendingSecureRequest:
        """Create one pending secure request row."""

        row = PendingSecureRequest(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            question_id=question_id,
            secure_field=secure_field,
            integration_name=integration_name,
            credential_name=credential_name,
            credential_profile_key=credential_profile_key,
            tool_name=(tool_name or "").strip(),
            nonce=nonce,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_active(
        self,
        *,
        profile_id: str,
        session_id: str,
        question_id: str,
        secure_field: str,
        nonce: str,
    ) -> PendingSecureRequest | None:
        """Return active pending request when identifiers match and not expired/consumed."""

        statement: Select[tuple[PendingSecureRequest]] = (
            select(PendingSecureRequest)
            .where(
                PendingSecureRequest.profile_id == profile_id,
                PendingSecureRequest.session_id == session_id,
                PendingSecureRequest.question_id == question_id,
                PendingSecureRequest.secure_field == secure_field,
                PendingSecureRequest.nonce == nonce,
            )
            .order_by(PendingSecureRequest.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if row.consumed_at is not None or expires_at <= now:
            return None
        return row

    async def mark_consumed(self, *, row_id: int) -> None:
        """Mark pending request consumed."""

        row = await self._session.get(PendingSecureRequest, row_id)
        if row is None:
            return
        row.consumed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def claim_active(
        self,
        *,
        profile_id: str,
        session_id: str,
        question_id: str,
        secure_field: str,
        nonce: str,
    ) -> PendingSecureRequest | None:
        """Atomically claim one active pending request and mark it consumed."""

        now = datetime.now(timezone.utc)
        statement = (
            update(PendingSecureRequest)
            .where(
                PendingSecureRequest.profile_id == profile_id,
                PendingSecureRequest.session_id == session_id,
                PendingSecureRequest.question_id == question_id,
                PendingSecureRequest.secure_field == secure_field,
                PendingSecureRequest.nonce == nonce,
                PendingSecureRequest.consumed_at.is_(None),
                PendingSecureRequest.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(PendingSecureRequest)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        await self._session.flush()
        return row

    async def release_claim(self, *, row_id: int) -> None:
        """Release consumed marker after failed secure submit side effects."""

        row = await self._session.get(PendingSecureRequest, row_id)
        if row is None:
            return
        row.consumed_at = None
        await self._session.flush()
