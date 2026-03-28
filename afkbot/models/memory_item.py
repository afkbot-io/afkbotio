"""Scoped semantic memory item model."""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class MemoryItem(Base, TimestampMixin):
    """Persisted semantic memory row with scope metadata and stored embedding."""

    __tablename__ = "memory_item"
    __table_args__ = (
        UniqueConstraint("profile_id", "memory_key", name="uq_memory_profile_key"),
        Index("ix_memory_profile_scope_key", "profile_id", "scope_key"),
        Index("ix_memory_profile_visibility", "profile_id", "visibility"),
        Index("ix_memory_profile_updated", "profile_id", "updated_at", "id"),
        Index("ix_memory_item_logical_key", "logical_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    # Internal storage key combines logical key + scope digest. Tool/CLI users should read `logical_key`.
    memory_key: Mapped[str] = mapped_column(String(255))
    logical_key: Mapped[str] = mapped_column(String(128))
    scope_key: Mapped[str] = mapped_column(String(255))
    scope_kind: Mapped[str] = mapped_column(String(32))
    transport: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    peer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), default="manual")
    memory_kind: Mapped[str] = mapped_column(String(32), default="note")
    visibility: Mapped[str] = mapped_column(String(32), default="local")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
