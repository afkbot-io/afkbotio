"""Profile policy model."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin
from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS


class ProfilePolicy(Base, TimestampMixin):
    """Security and runtime policy bound to a profile."""

    __tablename__ = "profile_policy"

    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), primary_key=True)
    policy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    policy_preset: Mapped[str] = mapped_column(String(16), default="medium")
    policy_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    max_iterations_main: Mapped[int] = mapped_column(Integer, default=DEFAULT_LLM_MAX_ITERATIONS)
    max_iterations_subagent: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_LLM_MAX_ITERATIONS,
    )
    allowed_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    denied_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_directories_json: Mapped[str] = mapped_column(Text, default="[]")
    shell_allowed_commands_json: Mapped[str] = mapped_column(Text, default="[]")
    shell_denied_commands_json: Mapped[str] = mapped_column(Text, default="[]")
    network_allowlist_json: Mapped[str] = mapped_column(Text, default="[]")
