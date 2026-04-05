"""Deduplication cursor for human Task Flow notification channels."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskNotificationCursor(Base, TimestampMixin):
    """Per-actor cursor used to avoid repeating the same notification events."""

    __tablename__ = "task_notification_cursor"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "actor_type",
            "actor_ref",
            "channel",
            name="uq_task_notification_cursor_actor_channel",
        ),
        Index(
            "ix_task_notification_cursor_profile_actor",
            "profile_id",
            "actor_type",
            "actor_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(64))
    last_seen_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
