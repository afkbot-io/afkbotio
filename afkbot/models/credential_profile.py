"""Credential profile model for integration-scoped credential sets."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class CredentialProfile(Base, TimestampMixin):
    """Named credential set for one profile and integration."""

    __tablename__ = "credential_profile"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "integration_name",
            "profile_key",
            name="uq_credential_profile_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"))
    integration_name: Mapped[str] = mapped_column(String(64))
    profile_key: Mapped[str] = mapped_column(String(64), default="default")
    display_name: Mapped[str] = mapped_column(String(128), default="default")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
