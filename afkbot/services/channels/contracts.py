"""Channel transport contracts independent from routing or AgentLoop internals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChannelOutboundAttachmentKind = Literal[
    "photo",
    "document",
    "voice",
    "audio",
    "video",
    "animation",
    "sticker",
]


class ChannelDeliveryTarget(BaseModel):
    """Explicit outbound delivery target separate from execution profile/session."""

    model_config = ConfigDict(extra="forbid")

    transport: str = Field(min_length=1)
    binding_id: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    address: str | None = None
    subject: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_common_aliases(cls, value: object) -> object:
        """Accept common integration aliases before strict validation."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        transport = str(payload.get("transport") or "").strip().lower()
        chat_id = payload.pop("chat_id", None)
        if (
            chat_id is not None
            and payload.get("address") in {None, ""}
            and payload.get("peer_id") in {None, ""}
        ):
            if transport in {"telegram", "telegram_user"}:
                payload["peer_id"] = chat_id
            else:
                payload["address"] = chat_id
        if transport in {"telegram", "telegram_user"} and payload.get("peer_id") in {None, ""}:
            address = payload.get("address")
            if address not in {None, ""}:
                payload["peer_id"] = address
                payload["address"] = None
        return payload

    @field_validator("transport", mode="before")
    @classmethod
    def _normalize_transport(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("transport is required")
        return normalized

    @field_validator(
        "binding_id",
        "account_id",
        "peer_id",
        "thread_id",
        "user_id",
        "address",
        "subject",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _require_locator(self) -> "ChannelDeliveryTarget":
        if any(
            (
                self.binding_id,
                self.account_id,
                self.peer_id,
                self.thread_id,
                self.user_id,
                self.address,
            )
        ):
            return self
        raise ValueError(
            "delivery target requires binding_id or explicit channel coordinates"
        )


def build_delivery_target_runtime_metadata(
    target: ChannelDeliveryTarget | None,
) -> dict[str, str] | None:
    """Project one explicit delivery target into runtime metadata payload."""

    if target is None:
        return None
    payload = {
        "transport": target.transport,
        "binding_id": target.binding_id,
        "account_id": target.account_id,
        "peer_id": target.peer_id,
        "thread_id": target.thread_id,
        "user_id": target.user_id,
        "address": target.address,
        "subject": target.subject,
    }
    return {key: value for key, value in payload.items() if value is not None}


class ChannelDeliveryResult(BaseModel):
    """Structured result for one outbound channel delivery attempt."""

    model_config = ConfigDict(extra="forbid")

    transport: str
    target: dict[str, str]
    payload: dict[str, object] = Field(default_factory=dict)


class ChannelOutboundAttachment(BaseModel):
    """One media attachment requested for outbound channel delivery."""

    model_config = ConfigDict(extra="forbid")

    kind: ChannelOutboundAttachmentKind
    source: str = Field(min_length=1, max_length=4096)
    caption: str | None = Field(default=None, max_length=1024)
    parse_mode: str | None = Field(default=None, max_length=32)

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("attachment kind is required")
        return value.strip().lower()

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("attachment source is required")
        normalized = value.strip()
        if not normalized:
            raise ValueError("attachment source is required")
        return normalized

    @field_validator("caption", "parse_mode", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("attachment text fields must be strings")
        normalized = value.strip()
        return normalized or None


class ChannelOutboundMessage(BaseModel):
    """Structured outbound message payload shared by channel delivery adapters."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=200000)
    parse_mode: str | None = Field(default=None, max_length=32)
    disable_web_page_preview: bool = False
    reply_markup: dict[str, object] | None = None
    attachments: tuple[ChannelOutboundAttachment, ...] = ()
    stream_draft: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("message text must be a string")
        return value.strip()

    @field_validator("parse_mode", mode="before")
    @classmethod
    def _normalize_parse_mode(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("parse_mode must be a string")
        normalized = value.strip()
        return normalized or None

    @field_validator("reply_markup", mode="before")
    @classmethod
    def _normalize_reply_markup(cls, value: object) -> dict[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("reply_markup must be an object")
        return {str(key): item for key, item in value.items()}

    @model_validator(mode="after")
    def _require_content(self) -> "ChannelOutboundMessage":
        if self.text.strip() or self.attachments:
            return self
        raise ValueError("outbound message requires text or at least one attachment")


@dataclass(frozen=True, slots=True)
class ChannelDeliveryTelemetryEvent:
    """One recorded outbound delivery attempt."""

    transport: str
    ok: bool
    error_code: str | None = None
    binding_id: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    address: str | None = None
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelDeliveryTransportDiagnostics:
    """Aggregated delivery counters for one transport."""

    transport: str
    total: int
    succeeded: int
    failed: int


@dataclass(frozen=True, slots=True)
class ChannelDeliveryDiagnostics:
    """Aggregated outbound delivery diagnostics for operators."""

    total: int
    succeeded: int
    failed: int
    transports: tuple[ChannelDeliveryTransportDiagnostics, ...]
    recent_events: tuple[ChannelDeliveryTelemetryEvent, ...]
