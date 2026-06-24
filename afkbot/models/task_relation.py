"""First-class relation graph for Task Flow tasks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class TaskRelation(Base):
    """Typed edge between two related Task Flow tasks."""

    __tablename__ = "task_relation"
    __table_args__ = (
        UniqueConstraint(
            "source_task_id",
            "target_task_id",
            "relation_type",
            name="ux_task_relation_edge",
        ),
        Index("ix_task_relation_source", "source_task_id", "relation_type"),
        Index("ix_task_relation_target", "target_task_id", "relation_type"),
        Index("ix_task_relation_profile_flow", "profile_id", "flow_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    source_task_id: Mapped[str] = mapped_column(ForeignKey("task.id"))
    target_task_id: Mapped[str] = mapped_column(ForeignKey("task.id"))
    relation_type: Mapped[str] = mapped_column(String(48))
    is_blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    satisfied_on_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
