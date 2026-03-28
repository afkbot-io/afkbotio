"""Model for trusted pending interactive resume envelopes."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class PendingResumeEnvelope(Base, TimestampMixin):
    """Server-side trusted replay payload for one pending interactive envelope."""

    __tablename__ = "pending_resume_envelope"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_pending_resume_envelope_question_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    question_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64))
    secure_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    spec_patch_json: Mapped[str | None] = mapped_column(Text, nullable=True)
