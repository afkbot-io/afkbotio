"""Reusable automation node definition model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationNodeDefinition(Base, TimestampMixin):
    """Reusable node descriptor shared across flow versions."""

    __tablename__ = "automation_node_definition"
    __table_args__ = (
        UniqueConstraint("profile_id", "slug", name="ux_automation_node_definition_profile_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    slug: Mapped[str] = mapped_column(String(255))
    node_kind: Mapped[str] = mapped_column(String(32))
    node_type: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
