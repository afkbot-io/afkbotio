"""Model for short-lived connect access tokens."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ConnectAccessToken(Base, TimestampMixin):
    """Access token row used by authenticated chat API control plane."""

    __tablename__ = "connect_access_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_connect_access_token_hash"),
        Index("ix_connect_access_profile_session", "profile_id", "session_id"),
        Index("ix_connect_access_refresh_session", "refresh_session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    refresh_session_id: Mapped[int] = mapped_column(ForeignKey("connect_session_token.id"))
    base_url: Mapped[str] = mapped_column(String(2048))
    access_token_hash: Mapped[str] = mapped_column("token_hash", String(128))
    allow_diagnostics: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    runtime_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_overlay: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
