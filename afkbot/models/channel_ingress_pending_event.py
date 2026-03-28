"""Model for durable pending channel ingress batches."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChannelIngressPendingEvent(Base, TimestampMixin):
    """Stores pending inbound channel events until one batch flush succeeds."""

    __tablename__ = "channel_ingress_pending_event"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "event_key",
            name="uq_channel_ingress_pending_event_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("channel_endpoint.endpoint_id", ondelete="CASCADE"),
        index=True,
    )
    transport: Mapped[str] = mapped_column(String(64), index=True)
    conversation_key: Mapped[str] = mapped_column(String(512), index=True)
    event_key: Mapped[str] = mapped_column(String(256), index=True)
    message_id: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(128), index=True)
    peer_id: Mapped[str] = mapped_column(String(128), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[str] = mapped_column(String(64))
    chat_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
