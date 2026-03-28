"""Model for pending secure-field requests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class PendingSecureRequest(Base, TimestampMixin):
    """One pending secure prompt request bound to profile/session/run."""

    __tablename__ = "pending_secure_request"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_pending_secure_request_question_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    question_id: Mapped[str] = mapped_column(String(128), index=True)
    secure_field: Mapped[str] = mapped_column(String(128))
    integration_name: Mapped[str] = mapped_column(String(64))
    credential_name: Mapped[str] = mapped_column(String(128))
    credential_profile_key: Mapped[str] = mapped_column(String(64), default="default")
    tool_name: Mapped[str] = mapped_column(String(128), default="")
    nonce: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
