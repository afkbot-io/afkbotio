"""Durable sequence counters for session-sharded JSONL stores."""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class SessionStoreSequence(Base):
    """Per-session sequence allocator for materialized JSONL records."""

    __tablename__ = "session_store_sequence"

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    next_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
