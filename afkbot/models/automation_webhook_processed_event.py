"""Processed webhook event hashes for automation idempotency."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationWebhookProcessedEvent(Base, TimestampMixin):
    """One processed webhook event hash bound to an automation."""

    __tablename__ = "automation_webhook_processed_event"

    automation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("automation.id"),
        primary_key=True,
    )
    event_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
