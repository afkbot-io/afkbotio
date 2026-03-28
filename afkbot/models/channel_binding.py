"""Channel binding model mapping transport context to profile/session policy."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChannelBinding(Base, TimestampMixin):
    """Persisted routing rule from one transport scope to one profile."""

    __tablename__ = "channel_binding"

    binding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    transport: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    session_policy: Mapped[str] = mapped_column(String(32), default="main")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    peer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_overlay: Mapped[str | None] = mapped_column(Text, nullable=True)
