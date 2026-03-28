"""Persistent subagent task model for cross-process lifecycle."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class SubagentTask(Base):
    """Persisted subagent execution task metadata and result."""

    __tablename__ = "subagent_task"
    __table_args__ = (
        Index("ix_subagent_task_profile_session", "profile_id", "session_id"),
        Index("ix_subagent_task_status", "status"),
        Index("ix_subagent_task_finished_at", "finished_at"),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subagent_name: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text)
    timeout_sec: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    child_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
