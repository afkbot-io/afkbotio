"""Automation model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class Automation(Base, TimestampMixin):
    """Persisted automation descriptor bound to one profile."""

    __tablename__ = "automation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
