"""Persistent task model for the Task Flow domain."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    """Durable work item that may belong to one task flow."""

    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_profile_status", "profile_id", "status"),
        Index("ix_task_profile_owner_status", "profile_id", "owner_type", "owner_ref", "status"),
        Index("ix_task_profile_flow", "profile_id", "flow_id"),
        Index("ix_task_due_at", "due_at"),
        Index("ix_task_lease_until", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_flow.id"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="todo")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_ref: Mapped[str] = mapped_column(String(255))
    reviewer_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewer_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    labels_json: Mapped[str] = mapped_column(Text, default="[]")
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocked_reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_attempt: Mapped[int] = mapped_column(Integer, default=0)
    last_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_session_profile_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
