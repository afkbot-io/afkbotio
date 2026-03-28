"""Persisted session compaction summary model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChatSessionCompaction(Base, TimestampMixin):
    """Trusted compact summary for older turns in one chat session."""

    __tablename__ = "chat_session_compaction"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "profile_id"],
            ["chat_session.id", "chat_session.profile_id"],
            name="fk_chat_session_compaction_session_profile",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), primary_key=True)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    compacted_until_turn_id: Mapped[int] = mapped_column(Integer, default=0)
    source_turn_count: Mapped[int] = mapped_column(Integer, default=0)
    strategy: Mapped[str] = mapped_column(String(32), default="deterministic_v1")
