"""Runlog event model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class RunlogEvent(Base, TimestampMixin):
    """Audit event produced during run execution."""

    __tablename__ = "runlog_event"
    __table_args__ = (
        Index("ix_runlog_event_created_at", "created_at", "id"),
        Index("ix_runlog_event_run_id_id", "run_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_session.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
