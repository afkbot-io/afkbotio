"""Append-only event model for Task Flow task history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class TaskEvent(Base):
    """One immutable event in the lifecycle of a Task Flow task."""

    __tablename__ = "task_event"
    __table_args__ = (
        Index("ix_task_event_task_created", "task_id", "created_at"),
        Index("ix_task_event_created_at", "created_at", "id"),
        Index("ix_task_event_type", "event_type"),
        Index("ix_task_event_task_run", "task_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), index=True)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_run.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
