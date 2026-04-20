"""Automation graph node model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationNode(Base, TimestampMixin):
    """Concrete node instance placed inside one automation flow."""

    __tablename__ = "automation_node"
    __table_args__ = (UniqueConstraint("flow_id", "node_key", name="ux_automation_node_flow_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("automation_flow.id"), index=True)
    node_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_node_definition.id"),
        nullable=True,
    )
    node_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_node_version.id"),
        nullable=True,
    )
    node_key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    node_kind: Mapped[str] = mapped_column(String(32))
    node_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
