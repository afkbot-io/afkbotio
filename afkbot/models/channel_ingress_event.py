"""Model for adapter-level inbound event deduplication."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChannelIngressEvent(Base, TimestampMixin):
    """Stores processed inbound event keys per endpoint to avoid duplicate replies."""

    __tablename__ = "channel_ingress_event"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "event_key",
            name="uq_channel_ingress_event_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("channel_endpoint.endpoint_id", ondelete="CASCADE"),
        index=True,
    )
    transport: Mapped[str] = mapped_column(String(64), index=True)
    event_key: Mapped[str] = mapped_column(String(256), index=True)
