"""Persistent task flow container model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskFlow(Base, TimestampMixin):
    """Durable grouping container for related tasks."""

    __tablename__ = "task_flow"
    __table_args__ = (
        Index("ix_task_flow_profile_status", "profile_id", "status"),
        Index("ix_task_flow_profile_closed_at", "profile_id", "closed_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    default_owner_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_owner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    labels_json: Mapped[str] = mapped_column(Text, default="[]")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
