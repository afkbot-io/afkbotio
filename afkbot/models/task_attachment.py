"""Persistent attachment model for Task Flow tasks."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskAttachment(Base, TimestampMixin):
    """Binary attachment linked to one Task Flow task."""

    __tablename__ = "task_attachment"
    __table_args__ = (
        Index("ix_task_attachment_task_created", "task_id", "created_at"),
        Index("ix_task_attachment_profile_task", "profile_id", "task_id"),
        Index("ix_task_attachment_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="file")
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
    content: Mapped[bytes] = mapped_column(LargeBinary)
