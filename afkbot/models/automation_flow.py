"""Automation graph flow model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationFlow(Base, TimestampMixin):
    """Persisted graph definition container for one automation."""

    __tablename__ = "automation_flow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automation.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    layout_json: Mapped[str | None] = mapped_column(Text, nullable=True)
