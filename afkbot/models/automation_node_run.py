"""Automation graph per-node run ledger model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationNodeRun(Base, TimestampMixin):
    """Per-node execution record for one graph run."""

    __tablename__ = "automation_node_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("automation_run.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("automation_node.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    execution_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_ports_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    effects_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    child_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    child_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    child_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
