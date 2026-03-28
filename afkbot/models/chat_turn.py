"""Chat turn model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base


class ChatTurn(Base):
    """Single user/assistant turn in a chat session."""

    __tablename__ = "chat_turn"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "profile_id"],
            ["chat_session.id", "chat_session.profile_id"],
            name="fk_chat_turn_session_profile",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"))
    user_message: Mapped[str] = mapped_column(Text)
    assistant_message: Mapped[str] = mapped_column(Text)
