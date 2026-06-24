"""Exact-once delegation claims for Task Flow v2 decomposition."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskDelegationClaim(Base, TimestampMixin):
    """Accepted decomposition fingerprint and child task ids for one parent task."""

    __tablename__ = "task_delegation_claim"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "source_task_id",
            "plan_fingerprint",
            name="ux_task_delegation_claim_plan",
        ),
        Index("ix_task_delegation_claim_source_status", "source_task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    source_task_id: Mapped[str] = mapped_column(ForeignKey("task.id"))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="accepted")
    plan_fingerprint: Mapped[str] = mapped_column(String(128))
    work_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_task_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    request_json: Mapped[str] = mapped_column(Text, default="{}")
