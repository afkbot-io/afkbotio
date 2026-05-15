"""Append-only Task Flow document revision history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class TaskDocumentRevision(Base):
    """One immutable revision for a Task Flow document."""

    __tablename__ = "task_document_revision"
    __table_args__ = (
        UniqueConstraint("document_id", "revision", name="ux_task_document_revision_number"),
        Index("ix_task_document_revision_document", "document_id", "revision"),
        Index("ix_task_document_revision_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("task_document.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
