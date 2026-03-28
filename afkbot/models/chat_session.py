"""Chat session model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChatSession(Base, TimestampMixin):
    """Conversation container for chat turns."""

    __tablename__ = "chat_session"
    __table_args__ = (UniqueConstraint("id", "profile_id", name="uq_chat_session_id_profile"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"))
    title: Mapped[str] = mapped_column(String(255), default="Session")
    status: Mapped[str] = mapped_column(String(32), default="active")
