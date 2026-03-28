"""Repository for trusted pending interactive resume envelopes."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.pending_resume_envelope import PendingResumeEnvelope


class PendingResumeEnvelopeRepository:
    """Persistence operations for server-side replay-safe pending envelopes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        question_id: str,
        action: str,
        secure_field: str | None,
        spec_patch_json: str | None,
    ) -> PendingResumeEnvelope:
        """Create one trusted pending envelope row."""

        row = PendingResumeEnvelope(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            question_id=question_id,
            action=action,
            secure_field=(secure_field or "").strip() or None,
            spec_patch_json=spec_patch_json,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_latest(
        self,
        *,
        profile_id: str,
        session_id: str,
        question_id: str,
        action: str,
        secure_field: str | None = None,
    ) -> PendingResumeEnvelope | None:
        """Return latest trusted envelope row for one pending question."""

        statement: Select[tuple[PendingResumeEnvelope]] = (
            select(PendingResumeEnvelope)
            .where(
                PendingResumeEnvelope.profile_id == profile_id,
                PendingResumeEnvelope.session_id == session_id,
                PendingResumeEnvelope.question_id == question_id,
                PendingResumeEnvelope.action == action,
            )
            .order_by(PendingResumeEnvelope.id.desc())
            .limit(1)
        )
        normalized_secure_field = (secure_field or "").strip()
        if normalized_secure_field:
            statement = statement.where(PendingResumeEnvelope.secure_field == normalized_secure_field)
        return (await self._session.execute(statement)).scalar_one_or_none()
