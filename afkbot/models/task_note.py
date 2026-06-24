"""Meaningful Task Flow notes separated from chatty comments."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class TaskNote(Base):
    """Durable decision, blocker, handoff, evidence, review, or final note."""

    __tablename__ = "task_note"
    __table_args__ = (
        UniqueConstraint("task_id", "note_type", "content_hash", name="ux_task_note_dedupe"),
        Index("ix_task_note_task_created", "task_id", "created_at"),
        Index("ix_task_note_profile_flow", "profile_id", "flow_id"),
        Index("ix_task_note_type", "note_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), index=True)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_run.id"), nullable=True)
    note_type: Mapped[str] = mapped_column(String(48))
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
