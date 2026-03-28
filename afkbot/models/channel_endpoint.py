"""Persisted external channel endpoint configuration."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ChannelEndpoint(Base, TimestampMixin):
    """Configured external channel adapter instance bound to one profile-agent."""

    __tablename__ = "channel_endpoint"

    endpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    transport: Mapped[str] = mapped_column(String(64), index=True)
    adapter_kind: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    credential_profile_key: Mapped[str] = mapped_column(String(64), default="default")
    account_id: Mapped[str] = mapped_column(String(128), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    group_trigger_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
