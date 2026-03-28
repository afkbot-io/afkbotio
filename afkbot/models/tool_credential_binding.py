"""Credential binding model connecting profile/integration/tool and encrypted secret."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class ToolCredentialBinding(Base, TimestampMixin):
    """Mapping from profile/integration credential name to encrypted secret row."""

    __tablename__ = "tool_credential_binding"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "integration_name",
            "credential_profile_key",
            "tool_name",
            "credential_name",
            "is_active",
            name="uq_tool_credential_binding_active",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"))
    integration_name: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    credential_profile_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default"
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    credential_name: Mapped[str] = mapped_column(String(128))
    secret_id: Mapped[int] = mapped_column(ForeignKey("secret.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
