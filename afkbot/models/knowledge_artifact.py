"""Derived knowledge artifacts for Task Flow project work."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afkbot.models.base import Base, TimestampMixin


class KnowledgeArtifact(Base, TimestampMixin):
    """Materialized project knowledge with provenance back to source tables."""

    __tablename__ = "knowledge_artifact"
    __table_args__ = (
        UniqueConstraint("profile_id", "dedupe_key", name="uq_knowledge_artifact_profile_dedupe"),
        Index("ix_knowledge_artifact_profile_flow", "profile_id", "flow_id"),
        Index("ix_knowledge_artifact_task", "task_id", "task_run_id"),
        Index(
            "ix_knowledge_artifact_profile_task_active",
            "profile_id",
            "task_id",
            "status",
            "artifact_kind",
            "updated_at",
            "id",
        ),
        Index("ix_knowledge_artifact_scope", "profile_id", "scope_type", "scope_id"),
        Index("ix_knowledge_artifact_kind_status", "artifact_kind", "status"),
        Index("ix_knowledge_artifact_updated", "profile_id", "updated_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id"), index=True)
    flow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str] = mapped_column(String(255))
    artifact_kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    details_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_max_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_max_turn_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_fingerprint: Mapped[str] = mapped_column(String(128))
    dedupe_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")
