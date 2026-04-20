"""Automation graph edge model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class AutomationEdge(Base, TimestampMixin):
    """Directed edge between two nodes inside one flow."""

    __tablename__ = "automation_edge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("automation_flow.id"), index=True)
    source_node_id: Mapped[int] = mapped_column(ForeignKey("automation_node.id"), index=True)
    target_node_id: Mapped[int] = mapped_column(ForeignKey("automation_node.id"), index=True)
    source_port: Mapped[str] = mapped_column(String(64), default="default")
    target_port: Mapped[str] = mapped_column(String(64), default="default")
