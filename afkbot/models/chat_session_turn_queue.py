"""Per-session turn queue markers."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChatSessionTurnQueueItem(Base, TimestampMixin):
    """Durable marker for one in-flight turn request.

    The row intentionally stores only routing and lease metadata. The user message stays in the
    caller process while it waits for its turn, so queueing does not add another raw-message store.
    """

    __tablename__ = "chat_session_turn_queue"
    __table_args__ = (
        UniqueConstraint("owner_token", name="uq_chat_session_turn_queue_owner"),
        Index(
            "ix_chat_session_turn_queue_session_status_id",
            "profile_id",
            "session_id",
            "status",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    client_msg_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
