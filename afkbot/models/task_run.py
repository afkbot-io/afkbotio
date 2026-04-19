"""Execution attempt model for Task Flow tasks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskRun(Base, TimestampMixin):
    """One detached execution attempt for one task."""

    __tablename__ = "task_run"
    __table_args__ = (
        Index("ix_task_run_task_attempt", "task_id", "attempt"),
        Index("ix_task_run_finished_at", "finished_at", "id"),
        Index("ix_task_run_status", "status"),
        Index("ix_task_run_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_ref: Mapped[str] = mapped_column(String(255))
    execution_mode: Mapped[str] = mapped_column(String(32), default="detached")
    status: Mapped[str] = mapped_column(String(32), default="claimed")
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
