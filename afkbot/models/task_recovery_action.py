"""Autonomous recovery actions for blocked Task Flow work."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskRecoveryAction(Base, TimestampMixin):
    """One structured recovery path such as reassign, retry, review, or ask-human."""

    __tablename__ = "task_recovery_action"
    __table_args__ = (
        Index("ix_task_recovery_source_status", "source_task_id", "status"),
        Index("ix_task_recovery_profile_owner_status", "profile_id", "owner_type", "owner_ref", "status"),
        Index("ix_task_recovery_fingerprint", "profile_id", "fingerprint"),
        Index("ix_task_recovery_status_due", "status", "due_at"),
        Index(
            "ux_task_recovery_open_fingerprint",
            "profile_id",
            "fingerprint",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    source_task_id: Mapped[str] = mapped_column(ForeignKey("task.id"))
    recovery_task_id: Mapped[str | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="open")
    owner_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cause_code: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(128))
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
