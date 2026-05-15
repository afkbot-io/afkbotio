"""Editable Task Flow document model for flow and task knowledge."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class TaskDocument(Base):
    """Latest editable document body attached to a Task Flow scope."""

    __tablename__ = "task_document"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "scope_type",
            "scope_id",
            "document_key",
            name="ux_task_document_scope_key",
        ),
        Index("ix_task_document_profile_scope", "profile_id", "scope_type", "scope_id"),
        Index("ix_task_document_key", "document_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(String(64))
    document_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    confirmation_status: Mapped[str] = mapped_column(String(32), default="draft")
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_by_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirmed_by_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_document_revision.id"),
        nullable=True,
    )
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    updated_by_type: Mapped[str] = mapped_column(String(32))
    updated_by_ref: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
