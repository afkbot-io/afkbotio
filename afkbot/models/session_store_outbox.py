"""Transactional outbox for materialized session JSONL stores."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class SessionStoreOutbox(Base, TimestampMixin):
    """One committed JSONL mutation waiting to be materialized."""

    __tablename__ = "session_store_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    mutation_json: Mapped[str] = mapped_column(Text, nullable=False)
