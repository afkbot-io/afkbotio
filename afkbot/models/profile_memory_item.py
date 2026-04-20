"""Pinned profile/core memory item model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ProfileMemoryItem(Base, TimestampMixin):
    """Persisted high-signal core memory for one profile."""

    __tablename__ = "profile_memory_item"
    __table_args__ = (
        UniqueConstraint("profile_id", "memory_key", name="uq_profile_memory_profile_key"),
        Index("ix_profile_memory_updated", "profile_id", "updated_at", "id"),
        Index("ix_profile_memory_status_updated", "profile_id", "stale", "updated_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    memory_key: Mapped[str] = mapped_column(String(128))
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), default="manual")
    memory_kind: Mapped[str] = mapped_column(String(32), default="fact")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
