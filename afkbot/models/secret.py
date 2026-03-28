"""Secret model for encrypted credential values."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class Secret(Base, TimestampMixin):
    """Encrypted secret value stored in credentials vault."""

    __tablename__ = "secret"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    key_version: Mapped[str] = mapped_column(String(64))
