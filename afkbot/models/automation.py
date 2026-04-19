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
    execution_mode: Mapped[str] = mapped_column(String(16), default="prompt")
    graph_fallback_mode: Mapped[str] = mapped_column(
        String(32),
        default="resume_with_ai_if_safe",
    )
    delivery_mode: Mapped[str] = mapped_column(String(16), default="tool")
    delivery_target_json: Mapped[str | None] = mapped_column(Text, nullable=True)
