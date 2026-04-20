"""Automation graph run ledger model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationRun(Base, TimestampMixin):
    """Execution ledger for one automation graph run."""

    __tablename__ = "automation_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automation.id"), index=True)
    flow_id: Mapped[int | None] = mapped_column(ForeignKey("automation_flow.id"), nullable=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    parent_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
