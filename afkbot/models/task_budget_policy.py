"""Task Flow v2 budget policy model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskBudgetPolicy(Base, TimestampMixin):
    """Budget and fanout limits scoped to a profile, flow, task, or employee."""

    __tablename__ = "task_budget_policy"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "scope_type",
            "scope_id",
            "policy_key",
            name="ux_task_budget_policy_scope_key",
        ),
        Index("ix_task_budget_policy_profile_status", "profile_id", "status"),
        Index("ix_task_budget_policy_scope", "scope_type", "scope_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str] = mapped_column(String(255))
    policy_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    policy_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
