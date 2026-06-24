"""Budget and circuit-breaker incidents for Task Flow v2."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskBudgetIncident(Base, TimestampMixin):
    """One budget, loop, or expensive-action stop emitted by the control plane."""

    __tablename__ = "task_budget_incident"
    __table_args__ = (
        Index("ix_task_budget_incident_profile_status", "profile_id", "status"),
        Index("ix_task_budget_incident_task", "task_id", "status"),
        Index("ix_task_budget_incident_task_type", "task_id", "status", "incident_type"),
        Index("ix_task_budget_incident_fingerprint", "profile_id", "fingerprint"),
        Index(
            "ux_task_budget_incident_open_fingerprint",
            "profile_id",
            "fingerprint",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_run.id"), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("task_budget_policy.id"), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="open")
    fingerprint: Mapped[str] = mapped_column(String(128))
    reason_code: Mapped[str] = mapped_column(String(64))
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
