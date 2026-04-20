"""Automation observe-mode optimization snapshot model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationOptimizationSnapshot(Base, TimestampMixin):
    """Persisted structured optimization trace for one automation."""

    __tablename__ = "automation_optimization_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automation.id"), index=True)
    snapshot_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    snapshot_json: Mapped[str] = mapped_column(Text)
