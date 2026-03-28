"""Automation cron trigger model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class AutomationTriggerCron(Base):
    """Cron trigger settings for one automation."""

    __tablename__ = "automation_trigger_cron"

    automation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("automation.id"),
        primary_key=True,
    )
    cron_expr: Mapped[str] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
