"""Automation webhook trigger model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class AutomationTriggerWebhook(Base):
    """Webhook trigger settings for one automation."""

    __tablename__ = "automation_trigger_webhook"
    __table_args__ = (
        Index("ix_automation_webhook_token", "webhook_token", unique=True),
        Index("ix_automation_webhook_token_hash", "webhook_token_hash", unique=True),
    )

    automation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("automation.id"),
        primary_key=True,
    )
    webhook_token: Mapped[str] = mapped_column(String(255))
    webhook_token_hash: Mapped[str] = mapped_column(String(128))
    last_event_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    in_progress_event_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    in_progress_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
