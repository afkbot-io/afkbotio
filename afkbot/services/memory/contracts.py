"""Pydantic contracts and scope helpers for semantic memory."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

MemoryScopeKind = Literal["profile", "chat", "thread", "user_in_chat"]
MemoryScopeMode = Literal["auto", "profile", "chat", "thread", "user_in_chat"]
MemorySourceKind = Literal["manual", "auto", "watcher", "automation"]
MemoryKind = Literal["fact", "preference", "decision", "task", "risk", "note"]
MemoryVisibility = Literal["local", "promoted_global"]

_PROFILE_SCOPE_KEY = "profile"


class MemoryScopeDescriptor(BaseModel):
    """Normalized durable scope selectors for one semantic memory operation."""

    model_config = ConfigDict(extra="forbid")

    scope_kind: MemoryScopeKind = "profile"
    transport: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    binding_id: str | None = None

    @field_validator(
        "transport",
        "account_id",
        "peer_id",
        "thread_id",
        "user_id",
        "session_id",
        "binding_id",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_required_selectors(self) -> MemoryScopeDescriptor:
        if self.scope_kind == "profile":
            return self
        missing_chat_base = [
            name
            for name, value in (
                ("transport", self.transport),
                ("account_id", self.account_id),
                ("peer_id", self.peer_id),
            )
            if value is None
        ]
        if missing_chat_base:
            raise ValueError(
                f"{self.scope_kind} scope requires: {', '.join(missing_chat_base)}"
            )
        if self.scope_kind == "thread" and self.thread_id is None:
            raise ValueError("thread scope requires: thread_id")
        if self.scope_kind == "user_in_chat" and self.user_id is None:
            raise ValueError("user_in_chat scope requires: user_id")
        return self

    @classmethod
    def profile_scope(cls, *, session_id: str | None = None, binding_id: str | None = None) -> MemoryScopeDescriptor:
        """Return the canonical profile-global scope descriptor."""

        return cls(scope_kind="profile", session_id=session_id, binding_id=binding_id)

    @property
    def is_profile_scope(self) -> bool:
        """Return whether this descriptor targets profile-global memory."""

        return self.scope_kind == "profile"

    def scope_key(self) -> str:
        """Return deterministic stable scope key used for exact local filtering."""

        if self.scope_kind == "profile":
            return _PROFILE_SCOPE_KEY
        parts = [
            f"scope={self.scope_kind}",
            f"transport={self.transport}",
            f"account_id={self.account_id}",
            f"peer_id={self.peer_id}",
        ]
        if self.thread_id is not None:
            parts.append(f"thread_id={self.thread_id}")
        if self.user_id is not None:
            parts.append(f"user_id={self.user_id}")
        return "|".join(parts)

    def storage_key(self, logical_memory_key: str) -> str:
        """Return bounded internal storage key for one logical key within this scope."""

        scope_digest = hashlib.sha1(self.scope_key().encode("utf-8")).hexdigest()[:24]  # noqa: S324
        return f"{logical_memory_key}@{scope_digest}"


class MemoryItemMetadata(BaseModel):
    """Serialized memory row metadata for tool/cli responses."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_id: str
    memory_key: str
    scope_kind: MemoryScopeKind
    scope_key: str
    transport: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    binding_id: str | None = None
    source: str | None = None
    source_kind: MemorySourceKind
    memory_kind: MemoryKind
    visibility: MemoryVisibility
    summary: str | None = None
    details_md: str | None = None
    content: str
    score: float | None = None
    created_at: datetime
    updated_at: datetime


class MemoryGcResult(BaseModel):
    """Memory garbage-collection outcome counters."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    expired_deleted: int
    overflow_deleted: int
