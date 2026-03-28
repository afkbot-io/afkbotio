"""Model for chat API idempotency keys."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChatTurnIdempotency(Base, TimestampMixin):
    """Stores deterministic turn response per `(profile, session, client_msg_id)` key."""

    __tablename__ = "chat_turn_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "session_id",
            "client_msg_id",
            name="uq_chat_turn_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    client_msg_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    envelope_json: Mapped[str] = mapped_column(Text)


class ChatTurnIdempotencyClaim(Base, TimestampMixin):
    """Per-key execution claim to serialize parallel idempotent turn requests."""

    __tablename__ = "chat_turn_idempotency_claim"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "session_id",
            "client_msg_id",
            name="uq_chat_turn_idempotency_claim_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    client_msg_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
