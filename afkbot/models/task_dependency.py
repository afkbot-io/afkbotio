"""Task dependency edges for Task Flow orchestration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class TaskDependency(Base):
    """Directed dependency edge between two tasks."""

    __tablename__ = "task_dependency"
    __table_args__ = (
        Index("ix_task_dependency_depends_on", "depends_on_task_id"),
    )

    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), primary_key=True)
    depends_on_task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), primary_key=True)
    satisfied_on_status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
