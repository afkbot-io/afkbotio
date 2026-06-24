"""Tree and task holds for Task Flow control-plane stops."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskHold(Base, TimestampMixin):
    """Pause or hold scope for one flow, task, or task tree."""

    __tablename__ = "task_hold"
    __table_args__ = (
        Index("ix_task_hold_profile_status", "profile_id", "status"),
        Index("ix_task_hold_scope_status", "scope_type", "scope_id", "status"),
        Index("ix_task_hold_flow_status", "flow_id", "status"),
        Index("ix_task_hold_status_expires", "status", "expires_at"),
        Index(
            "ux_task_hold_active_scope",
            "profile_id",
            "scope_type",
            "scope_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    reason_code: Mapped[str] = mapped_column(String(64))
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    released_by_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    released_by_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
