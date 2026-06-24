"""Compact context digests for Task Flow v2 runtime packets."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class TaskContextDigest(Base, TimestampMixin):
    """Bounded summary used instead of replaying full task history into prompts."""

    __tablename__ = "task_context_digest"
    __table_args__ = (
        UniqueConstraint("task_id", "digest_key", name="ux_task_context_digest_key"),
        Index("ix_task_context_digest_profile_flow", "profile_id", "flow_id"),
        Index("ix_task_context_digest_hash", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("task_flow.id"), nullable=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("task.id"), index=True)
    digest_key: Mapped[str] = mapped_column(String(64), default="runtime")
    body: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    source_max_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_document_watermark: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_state_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_ref: Mapped[str] = mapped_column(String(255))
