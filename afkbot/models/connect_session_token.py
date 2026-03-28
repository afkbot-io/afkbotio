"""Model for desktop connect refresh sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ConnectSessionToken(Base, TimestampMixin):
    """Refresh-session row used by `/v1/connect/refresh` and `/v1/connect/revoke`."""

    __tablename__ = "connect_session_token"
    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name="uq_connect_session_refresh_token_hash"),
        Index("ix_connect_session_profile_session", "profile_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    base_url: Mapped[str] = mapped_column(String(2048))
    refresh_token_hash: Mapped[str] = mapped_column(String(128), index=True)
    session_proof_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    allow_diagnostics: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    runtime_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_overlay: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
