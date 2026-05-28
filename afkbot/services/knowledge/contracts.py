"""Contracts for derived project knowledge."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

KnowledgeArtifactKind = Literal["task_crystal"]
KnowledgeArtifactStatus = Literal["active", "superseded", "rejected", "stale"]
KnowledgeScopeType = Literal["task"]


class KnowledgeSourceRef(BaseModel):
    """Pointer from derived knowledge back to one source row or runtime artifact."""

    model_config = ConfigDict(frozen=True)

    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=255)
    source_version: str | None = Field(default=None, max_length=128)


class KnowledgeArtifactInput(BaseModel):
    """Validated payload used to create or update a knowledge artifact."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    flow_id: str | None = None
    task_id: str | None = None
    task_run_id: int | None = None
    scope_type: KnowledgeScopeType
    scope_id: str = Field(min_length=1, max_length=255)
    artifact_kind: KnowledgeArtifactKind
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    details_md: str | None = None
    source_refs: tuple[KnowledgeSourceRef, ...] = ()
    tags: tuple[str, ...] = ()
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    confirmed: bool = False
    source_max_event_id: int | None = None
    source_max_turn_id: int | None = None
    source_revision: int | None = None
    source_fingerprint: str = Field(min_length=1, max_length=128)
    dedupe_key: str = Field(min_length=1, max_length=255)
    status: KnowledgeArtifactStatus = "active"

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            tag = " ".join(str(item).split()).lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag[:64])
        return tuple(normalized)


class KnowledgeArtifactMetadata(BaseModel):
    """Serializable derived knowledge artifact."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str
    flow_id: str | None = None
    task_id: str | None = None
    task_run_id: int | None = None
    scope_type: str
    scope_id: str
    artifact_kind: str
    title: str
    summary: str
    details_md: str | None = None
    source_refs: tuple[KnowledgeSourceRef, ...] = ()
    tags: tuple[str, ...] = ()
    confidence: float
    confirmed: bool
    source_max_event_id: int | None = None
    source_max_turn_id: int | None = None
    source_revision: int | None = None
    source_fingerprint: str
    dedupe_key: str
    status: str
