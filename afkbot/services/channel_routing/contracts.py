"""Contracts for channel-to-profile routing and session policy resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SessionPolicy = Literal["main", "per-chat", "per-thread", "per-user-in-group"]


class ChannelBindingRule(BaseModel):
    """One routing rule from transport context to target profile/session policy."""

    model_config = ConfigDict(extra="forbid")

    binding_id: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    session_policy: SessionPolicy = "main"
    priority: int = 0
    enabled: bool = True
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    prompt_overlay: str | None = None

    @field_validator("transport", mode="before")
    @classmethod
    def _normalize_transport(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @field_validator("profile_id", "account_id", "peer_id", "thread_id", "user_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChannelRoutingInput(BaseModel):
    """Normalized inbound transport context used by routing."""

    model_config = ConfigDict(extra="forbid")

    transport: str = Field(min_length=1)
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    default_session_id: str = Field(default="main", min_length=1)

    @field_validator("transport", mode="before")
    @classmethod
    def _normalize_input_transport(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @field_validator("account_id", "peer_id", "thread_id", "user_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChannelRoutingDecision(BaseModel):
    """Resolved routing target for one inbound channel event."""

    model_config = ConfigDict(extra="forbid")

    binding_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    session_policy: SessionPolicy
    session_id: str = Field(min_length=1)
    prompt_overlay: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelRoutingTelemetryEvent:
    """Structured final routing outcome recorded for diagnostics."""

    transport: str
    strict: bool
    matched: bool
    no_match: bool
    fallback_used: bool
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    binding_id: str | None = None
    profile_id: str | None = None
    session_policy: SessionPolicy | None = None
    prompt_overlay_applied: bool = False


@dataclass(frozen=True, slots=True)
class ChannelRoutingTransportDiagnostics:
    """Aggregate routing counters for one normalized transport."""

    transport: str
    total: int
    matched: int
    fallback_used: int
    no_match: int
    strict_no_match: int


@dataclass(frozen=True, slots=True)
class ChannelRoutingDiagnostics:
    """Aggregated routing telemetry snapshot for one runtime root."""

    total: int
    matched: int
    fallback_used: int
    no_match: int
    strict_no_match: int
    transports: tuple[ChannelRoutingTransportDiagnostics, ...]
    recent_events: tuple[ChannelRoutingTelemetryEvent, ...]
