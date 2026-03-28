"""Run model."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class Run(Base, TimestampMixin):
    """Runtime execution for a single agent turn."""

    __tablename__ = "run"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "profile_id"],
            ["chat_session.id", "chat_session.profile_id"],
            name="fk_run_session_profile",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
