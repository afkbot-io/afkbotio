"""Automation node versioned artifact model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationNodeVersion(Base, TimestampMixin):
    """Versioned implementation contract for one node definition."""

    __tablename__ = "automation_node_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_definition_id: Mapped[int] = mapped_column(
        ForeignKey("automation_node_definition.id"),
        index=True,
    )
    version_label: Mapped[str] = mapped_column(String(64))
    runtime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tests_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_code: Mapped[str | None] = mapped_column(Text, nullable=True)
