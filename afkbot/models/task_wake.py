"""Claimable wake requests for Task Flow v2 execution."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskWake(Base, TimestampMixin):
    """One idempotent request to let an employee runtime inspect a task."""

    __tablename__ = "task_wake"
    __table_args__ = (
        UniqueConstraint("profile_id", "idempotency_key", name="ux_task_wake_profile_key"),
        Index(
            "ux_task_wake_open_natural_key",
            "task_id",
            "owner_type",
            "owner_ref",
            "reason_code",
            unique=True,
            sqlite_where=text("status IN ('pending', 'claimed')"),
            postgresql_where=text("status IN ('pending', 'claimed')"),
        ),
        Index("ix_task_wake_status_run_after", "status", "run_after", "priority", "created_at"),
        Index("ix_task_wake_task_status", "task_id", "status"),
        Index("ix_task_wake_profile_owner_status", "profile_id", "owner_type", "owner_ref", "status"),
        Index("ix_task_wake_task_run", "task_run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_ref: Mapped[str] = mapped_column(String(255))
    reason_code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    source_event_id: Mapped[int | None] = mapped_column(ForeignKey("task_event.id"), nullable=True)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_run.id"), nullable=True)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coalesced_count: Mapped[int] = mapped_column(Integer, default=0)
    last_coalesced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
