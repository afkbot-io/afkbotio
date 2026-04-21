"""Persisted external channel endpoint contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.tool_profiles import (
    ChannelToolProfile,
    normalize_channel_tool_profile,
)
from afkbot.services.profile_id import validate_profile_id

_CHANNEL_ENDPOINT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN = 100
CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX = 60_000
CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN = 0
CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX = 3_600
CHANNEL_INGRESS_BATCH_SIZE_MIN = 1
CHANNEL_INGRESS_BATCH_SIZE_MAX = 200
CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN = 256
CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX = 200_000
CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MIN = 0
CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MAX = 60_000
CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MIN = 0
CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MAX = 120_000
CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MIN = 1
CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MAX = 120
TELETHON_WATCHER_BATCH_INTERVAL_SEC_MIN = 10
TELETHON_WATCHER_BATCH_INTERVAL_SEC_MAX = 86_400
TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MIN = 10
TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MAX = 86_400
TELETHON_WATCHER_BATCH_SIZE_MIN = 1
TELETHON_WATCHER_BATCH_SIZE_MAX = 1_000
TELETHON_WATCHER_BUFFER_SIZE_MIN = 1
TELETHON_WATCHER_BUFFER_SIZE_MAX = 5_000
TELETHON_WATCHER_MESSAGE_CHARS_MIN = 32
TELETHON_WATCHER_MESSAGE_CHARS_MAX = 4_000
PARTYFLOW_CONTEXT_SIZE_MIN = 1
PARTYFLOW_CONTEXT_SIZE_MAX = 50
PARTYFLOW_TRIGGER_KEYWORDS_MAX = 20
PARTYFLOW_TRIGGER_KEYWORD_LENGTH_MIN = 2
PARTYFLOW_TRIGGER_KEYWORD_LENGTH_MAX = 100
TelegramGroupTriggerMode = Literal["mention_or_reply", "reply_only", "mention_only", "all_messages"]
TelethonReplyMode = Literal["same_chat", "disabled"]
TelethonGroupInvocationMode = Literal[
    "reply_or_command", "reply_only", "command_only", "all_messages"
]
PartyFlowIngressMode = Literal["webhook"]
PartyFlowTriggerMode = Literal["all", "mention", "keywords"]
PartyFlowReplyMode = Literal["same_conversation", "disabled"]


def _normalize_chat_pattern_values(value: object, *, error_label: str) -> tuple[str, ...]:
    """Normalize one persisted list of case-insensitive chat-name filter patterns."""

    raw_values: tuple[str, ...]
    if value is None:
        return ()
    if isinstance(value, str):
        raw_values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = tuple(str(item) for item in value)
    else:
        raise ValueError(f"{error_label} must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        pattern = item.strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        normalized.append(pattern)
    return tuple(normalized)


def validate_channel_endpoint_id(raw: str) -> str:
    """Validate one stable channel endpoint id."""

    normalized = raw.strip().lower()
    if not _CHANNEL_ENDPOINT_ID_RE.fullmatch(normalized):
        raise ValueError(
            "Invalid channel id. Use 1-64 chars: lowercase letters, digits, hyphen; must start with a letter or digit."
        )
    return normalized


class ChannelEndpointConfig(BaseModel):
    """Persisted adapter instance configuration."""

    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(min_length=1, max_length=64)
    transport: str = Field(min_length=1)
    adapter_kind: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    credential_profile_key: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    enabled: bool = True
    group_trigger_mode: str | None = None
    tool_profile: ChannelToolProfile = "inherit"
    config: dict[str, object] = Field(default_factory=dict)

    @field_validator("endpoint_id", mode="before")
    @classmethod
    def _normalize_endpoint_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("channel id is required")
        return validate_channel_endpoint_id(value)

    @field_validator(
        "transport", "adapter_kind", "credential_profile_key", "account_id", mode="before"
    )
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("text value is required")
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("text value is required")
        return normalized

    @field_validator("profile_id", mode="before")
    @classmethod
    def _normalize_profile_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("profile id is required")
        return validate_profile_id(value.strip().lower())

    @field_validator("group_trigger_mode", mode="before")
    @classmethod
    def _normalize_optional_selector(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("text value is required")
        normalized = value.strip().lower()
        return normalized or None

    @field_validator("tool_profile", mode="before")
    @classmethod
    def _normalize_tool_profile(cls, value: object) -> ChannelToolProfile:
        return normalize_channel_tool_profile(value)

    @field_validator("config", mode="before")
    @classmethod
    def _normalize_config(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("config must be an object")
        return {str(key): item for key, item in value.items()}


class ChannelIngressBatchConfig(BaseModel):
    """Typed config for delayed ingress coalescing before one AgentLoop turn."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    debounce_ms: int = Field(
        default=1500,
        ge=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
        le=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
    )
    cooldown_sec: int = Field(
        default=0,
        ge=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
        le=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
    )
    max_batch_size: int = Field(
        default=20,
        ge=CHANNEL_INGRESS_BATCH_SIZE_MIN,
        le=CHANNEL_INGRESS_BATCH_SIZE_MAX,
    )
    max_buffer_chars: int = Field(
        default=12_000,
        ge=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
        le=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
    )

    @model_validator(mode="after")
    def _validate_limits(self) -> "ChannelIngressBatchConfig":
        if self.cooldown_sec > 0 and not self.enabled:
            raise ValueError("ingress_batch cooldown_sec requires enabled=true")
        if self.max_buffer_chars < self.max_batch_size * 16:
            raise ValueError(
                "ingress_batch max_buffer_chars is too small for the selected max_batch_size"
            )
        return self


class ChannelReplyHumanizationConfig(BaseModel):
    """Typed config for Telegram-style delayed replies and typing simulation."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    min_delay_ms: int = Field(
        default=1_000,
        ge=CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MIN,
        le=CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MAX,
    )
    max_delay_ms: int = Field(
        default=8_000,
        ge=CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MIN,
        le=CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MAX,
    )
    chars_per_second: int = Field(
        default=12,
        ge=CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MIN,
        le=CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MAX,
    )

    @model_validator(mode="after")
    def _validate_delay_range(self) -> "ChannelReplyHumanizationConfig":
        if self.max_delay_ms < self.min_delay_ms:
            raise ValueError("reply_humanization max_delay_ms must be >= min_delay_ms")
        return self


class TelethonWatcherConfig(BaseModel):
    """Typed config for periodic Telethon watched-dialog digests."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    unmuted_only: bool = True
    include_private: bool = True
    include_groups: bool = True
    include_channels: bool = True
    batch_interval_sec: int = Field(
        default=300,
        ge=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MIN,
        le=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MAX,
    )
    dialog_refresh_interval_sec: int = Field(
        default=300,
        ge=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MIN,
        le=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MAX,
    )
    max_batch_size: int = Field(
        default=100,
        ge=TELETHON_WATCHER_BATCH_SIZE_MIN,
        le=TELETHON_WATCHER_BATCH_SIZE_MAX,
    )
    max_buffer_size: int = Field(
        default=500,
        ge=TELETHON_WATCHER_BUFFER_SIZE_MIN,
        le=TELETHON_WATCHER_BUFFER_SIZE_MAX,
    )
    max_message_chars: int = Field(
        default=1_000,
        ge=TELETHON_WATCHER_MESSAGE_CHARS_MIN,
        le=TELETHON_WATCHER_MESSAGE_CHARS_MAX,
    )
    blocked_chat_patterns: tuple[str, ...] = ()
    allowed_chat_patterns: tuple[str, ...] = ()
    delivery_target: ChannelDeliveryTarget | None = None
    delivery_credential_profile_key: str | None = None

    @field_validator("blocked_chat_patterns", "allowed_chat_patterns", mode="before")
    @classmethod
    def _normalize_patterns(
        cls,
        value: object,
    ) -> tuple[str, ...]:
        return _normalize_chat_pattern_values(value, error_label="watcher patterns")

    @field_validator("delivery_credential_profile_key", mode="before")
    @classmethod
    def _normalize_delivery_credential_profile_key(
        cls,
        value: object,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("delivery_credential_profile_key must be a string")
        normalized = value.strip().lower()
        return normalized or None

    @model_validator(mode="after")
    def _validate_enabled_scope(self) -> "TelethonWatcherConfig":
        if self.max_buffer_size < self.max_batch_size:
            raise ValueError("watcher max_buffer_size must be >= max_batch_size")
        if self.enabled and not any(
            (
                self.include_private,
                self.include_groups,
                self.include_channels,
            )
        ):
            raise ValueError("watcher requires at least one enabled source kind")
        if (
            self.delivery_target is not None
            and self.delivery_target.transport != "telegram_user"
            and self.delivery_credential_profile_key is None
        ):
            raise ValueError(
                "watcher delivery_credential_profile_key is required for non-telegram_user delivery targets"
            )
        return self


class TelethonUserEndpointConfig(ChannelEndpointConfig):
    """Typed Telethon userbot endpoint config."""

    transport: str = "telegram_user"
    adapter_kind: str = "telethon_userbot"
    group_trigger_mode: None = None
    reply_mode: TelethonReplyMode = "disabled"
    reply_blocked_chat_patterns: tuple[str, ...] = ()
    reply_allowed_chat_patterns: tuple[str, ...] = ()
    group_invocation_mode: TelethonGroupInvocationMode = "reply_or_command"
    process_self_commands: bool = False
    command_prefix: str = Field(default=".afk", min_length=1, max_length=32)
    ingress_batch: ChannelIngressBatchConfig = Field(default_factory=ChannelIngressBatchConfig)
    reply_humanization: ChannelReplyHumanizationConfig = Field(
        default_factory=ChannelReplyHumanizationConfig
    )
    mark_read_before_reply: bool = True
    watcher: TelethonWatcherConfig = Field(default_factory=TelethonWatcherConfig)

    @model_validator(mode="before")
    @classmethod
    def _inflate_config(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        config = payload.get("config")
        if isinstance(config, Mapping):
            if "tool_profile" not in payload and "tool_profile" in config:
                payload["tool_profile"] = config["tool_profile"]
            for key in (
                "reply_mode",
                "reply_blocked_chat_patterns",
                "reply_allowed_chat_patterns",
                "group_invocation_mode",
                "process_self_commands",
                "command_prefix",
                "ingress_batch",
                "reply_humanization",
                "mark_read_before_reply",
                "watcher",
            ):
                if key not in payload and key in config:
                    payload[key] = config[key]
        return payload

    @field_validator("command_prefix", mode="before")
    @classmethod
    def _normalize_command_prefix(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("command_prefix is required")
        normalized = value.strip()
        if not normalized:
            raise ValueError("command_prefix is required")
        return normalized

    @field_validator("reply_blocked_chat_patterns", "reply_allowed_chat_patterns", mode="before")
    @classmethod
    def _normalize_reply_patterns(cls, value: object) -> tuple[str, ...]:
        return _normalize_chat_pattern_values(value, error_label="reply patterns")

    def storage_config(self) -> dict[str, object]:
        """Return config payload persisted in `channel_endpoint.config_json`."""

        return {
            "tool_profile": self.tool_profile,
            "reply_mode": self.reply_mode,
            "reply_blocked_chat_patterns": self.reply_blocked_chat_patterns,
            "reply_allowed_chat_patterns": self.reply_allowed_chat_patterns,
            "group_invocation_mode": self.group_invocation_mode,
            "process_self_commands": self.process_self_commands,
            "command_prefix": self.command_prefix,
            "ingress_batch": self.ingress_batch.model_dump(mode="python", exclude_none=True),
            "reply_humanization": self.reply_humanization.model_dump(
                mode="python", exclude_none=True
            ),
            "mark_read_before_reply": self.mark_read_before_reply,
            "watcher": self.watcher.model_dump(mode="python", exclude_none=True),
        }


class TelegramPollingEndpointConfig(ChannelEndpointConfig):
    """Typed Telegram Bot API polling endpoint config."""

    transport: str = "telegram"
    adapter_kind: str = "telegram_bot_polling"
    group_trigger_mode: TelegramGroupTriggerMode = "mention_or_reply"
    ingress_batch: ChannelIngressBatchConfig = Field(default_factory=ChannelIngressBatchConfig)
    reply_humanization: ChannelReplyHumanizationConfig = Field(
        default_factory=ChannelReplyHumanizationConfig
    )

    @model_validator(mode="before")
    @classmethod
    def _inflate_config(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        config = payload.get("config")
        if isinstance(config, Mapping):
            if "tool_profile" not in payload and "tool_profile" in config:
                payload["tool_profile"] = config["tool_profile"]
            if "ingress_batch" not in payload and "ingress_batch" in config:
                payload["ingress_batch"] = config["ingress_batch"]
            if "reply_humanization" not in payload and "reply_humanization" in config:
                payload["reply_humanization"] = config["reply_humanization"]
        return payload

    def storage_config(self) -> dict[str, object]:
        """Return config payload persisted in `channel_endpoint.config_json`."""

        return {
            "tool_profile": self.tool_profile,
            "ingress_batch": self.ingress_batch.model_dump(mode="python", exclude_none=True),
            "reply_humanization": self.reply_humanization.model_dump(
                mode="python", exclude_none=True
            ),
        }


class PartyFlowWebhookEndpointConfig(ChannelEndpointConfig):
    """Typed PartyFlow outgoing-webhook endpoint config."""

    transport: str = "partyflow"
    adapter_kind: str = "partyflow_webhook"
    group_trigger_mode: None = None
    ingress_mode: PartyFlowIngressMode = "webhook"
    trigger_mode: PartyFlowTriggerMode = "mention"
    trigger_keywords: tuple[str, ...] = ()
    include_context: bool = True
    context_size: int = Field(
        default=8, ge=PARTYFLOW_CONTEXT_SIZE_MIN, le=PARTYFLOW_CONTEXT_SIZE_MAX
    )
    reply_mode: PartyFlowReplyMode = "same_conversation"
    ingress_batch: ChannelIngressBatchConfig = Field(default_factory=ChannelIngressBatchConfig)

    @model_validator(mode="before")
    @classmethod
    def _inflate_config(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        config = payload.get("config")
        if isinstance(config, Mapping):
            if "tool_profile" not in payload and "tool_profile" in config:
                payload["tool_profile"] = config["tool_profile"]
            for key in (
                "ingress_mode",
                "trigger_mode",
                "trigger_keywords",
                "include_context",
                "context_size",
                "reply_mode",
                "ingress_batch",
            ):
                if key not in payload and key in config:
                    payload[key] = config[key]
        return payload

    @field_validator("trigger_keywords", mode="before")
    @classmethod
    def _normalize_trigger_keywords(cls, value: object) -> tuple[str, ...]:
        return _normalize_chat_pattern_values(value, error_label="partyflow trigger keywords")

    @model_validator(mode="after")
    def _validate_trigger_config(self) -> "PartyFlowWebhookEndpointConfig":
        if self.trigger_mode == "keywords" and not self.trigger_keywords:
            raise ValueError("trigger_keywords are required when trigger_mode=keywords")
        if len(self.trigger_keywords) > PARTYFLOW_TRIGGER_KEYWORDS_MAX:
            raise ValueError(
                f"trigger_keywords support at most {PARTYFLOW_TRIGGER_KEYWORDS_MAX} values"
            )
        for keyword in self.trigger_keywords:
            if not (
                PARTYFLOW_TRIGGER_KEYWORD_LENGTH_MIN
                <= len(keyword)
                <= PARTYFLOW_TRIGGER_KEYWORD_LENGTH_MAX
            ):
                raise ValueError(
                    "each trigger keyword must be between "
                    f"{PARTYFLOW_TRIGGER_KEYWORD_LENGTH_MIN} and "
                    f"{PARTYFLOW_TRIGGER_KEYWORD_LENGTH_MAX} characters"
                )
        return self

    def storage_config(self) -> dict[str, object]:
        """Return config payload persisted in `channel_endpoint.config_json`."""

        return {
            "tool_profile": self.tool_profile,
            "ingress_mode": self.ingress_mode,
            "trigger_mode": self.trigger_mode,
            "trigger_keywords": self.trigger_keywords,
            "include_context": self.include_context,
            "context_size": self.context_size,
            "reply_mode": self.reply_mode,
            "ingress_batch": self.ingress_batch.model_dump(mode="python", exclude_none=True),
        }


def serialize_endpoint_storage_payload(
    config: ChannelEndpointConfig,
) -> tuple[str | None, dict[str, object]]:
    """Return normalized legacy/group column and adapter-specific config payload."""

    if isinstance(config, TelethonUserEndpointConfig):
        return None, config.storage_config()
    if isinstance(config, TelegramPollingEndpointConfig):
        return config.group_trigger_mode, config.storage_config()
    if isinstance(config, PartyFlowWebhookEndpointConfig):
        return None, config.storage_config()
    return config.group_trigger_mode, dict(config.config)


def deserialize_endpoint_config(payload: Mapping[str, object]) -> ChannelEndpointConfig:
    """Build typed endpoint config from raw persistence payload."""

    adapter_kind = str(payload.get("adapter_kind") or "").strip().lower()
    transport = str(payload.get("transport") or "").strip().lower()
    if adapter_kind == "telethon_userbot" or transport == "telegram_user":
        return TelethonUserEndpointConfig.model_validate(payload)
    if adapter_kind == "telegram_bot_polling" or transport == "telegram":
        return TelegramPollingEndpointConfig.model_validate(payload)
    if adapter_kind == "partyflow_webhook" or transport == "partyflow":
        return PartyFlowWebhookEndpointConfig.model_validate(payload)
    return ChannelEndpointConfig.model_validate(payload)
