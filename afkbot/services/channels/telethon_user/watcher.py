"""Pure helpers for Telethon watched-dialog batching and digest turns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channels import ChannelDeliveryTarget, build_delivery_target_runtime_metadata
from afkbot.services.channels.endpoint_contracts import TelethonWatcherConfig

WATCHER_NO_DIGEST_SENTINEL = "NO_DIGEST"
WATCHER_MEMORY_PEER_PREFIX = "__watcher__:"


@dataclass(frozen=True, slots=True)
class TelethonWatchedDialog:
    """One dialog eligible for periodic watcher digests."""

    chat_id: str
    chat_kind: str
    title: str


@dataclass(frozen=True, slots=True)
class TelethonWatchedEvent:
    """One buffered incoming message/post collected by the Telethon watcher."""

    event_key: str
    message_id: int
    chat_id: str
    chat_kind: str
    chat_title: str
    sender_id: str | None
    text: str
    observed_at: str


def select_watched_dialog(
    *,
    dialog: object,
    config: TelethonWatcherConfig,
    now: datetime | None = None,
) -> TelethonWatchedDialog | None:
    """Return watched-dialog metadata when one dialog matches watcher policy."""

    chat_kind = classify_dialog_kind(dialog)
    if chat_kind is None:
        return None
    if chat_kind == "private" and not config.include_private:
        return None
    if chat_kind == "group" and not config.include_groups:
        return None
    if chat_kind == "channel" and not config.include_channels:
        return None
    if config.unmuted_only and not dialog_notifications_enabled(dialog, now=now):
        return None
    title = normalize_dialog_title(dialog)
    match_text = build_dialog_match_text(dialog)
    if not matches_chat_title_filters(
        title=match_text,
        blocked_patterns=config.blocked_chat_patterns,
        allowed_patterns=config.allowed_chat_patterns,
    ):
        return None
    chat_id = normalize_chat_id(dialog)
    if chat_id is None:
        return None
    return TelethonWatchedDialog(
        chat_id=chat_id,
        chat_kind=chat_kind,
        title=title,
    )


def classify_dialog_kind(dialog: object) -> str | None:
    """Map one Telethon dialog into stable watcher chat kinds."""

    if bool(getattr(dialog, "is_user", False)):
        return "private"
    if bool(getattr(dialog, "is_group", False)):
        return "group"
    if bool(getattr(dialog, "is_channel", False)):
        return "channel"
    return None


def dialog_notifications_enabled(
    dialog: object,
    *,
    now: datetime | None = None,
) -> bool:
    """Return true when one dialog is not muted right now."""

    effective_now = now or datetime.now(UTC)
    notify_settings = getattr(getattr(dialog, "dialog", None), "notify_settings", None)
    mute_until = getattr(notify_settings, "mute_until", None)
    if mute_until is None:
        return True
    if isinstance(mute_until, datetime):
        muted_until = mute_until if mute_until.tzinfo is not None else mute_until.replace(tzinfo=UTC)
        return muted_until <= effective_now
    if isinstance(mute_until, (int, float)):
        if mute_until <= 0:
            return True
        return mute_until <= effective_now.timestamp()
    return True


def normalize_dialog_title(dialog: object) -> str:
    """Derive a stable human title for one Telethon dialog."""

    for candidate in (
        getattr(dialog, "name", None),
        getattr(dialog, "title", None),
        getattr(getattr(dialog, "entity", None), "title", None),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    chat_id = normalize_chat_id(dialog)
    return normalize_entity_title(getattr(dialog, "entity", None), fallback_id=chat_id)


def build_dialog_match_text(dialog: object) -> str:
    """Build matcher text for one dialog, including both human title and username when available."""

    chat_id = normalize_chat_id(dialog)
    for entity in (
        getattr(dialog, "entity", None),
        dialog,
    ):
        match_text = build_entity_match_text(entity, fallback_id=None)
        if match_text != "unknown-chat":
            return match_text
    return build_entity_match_text(None, fallback_id=chat_id)


def normalize_event_chat_title(event: object) -> str:
    """Derive a stable human title for one Telethon inbound event chat."""

    fallback_id = getattr(event, "chat_id", None)
    if bool(getattr(event, "is_private", False)):
        for entity in (
            getattr(event, "chat", None),
            getattr(event, "sender", None),
            getattr(getattr(event, "message", None), "chat", None),
            getattr(getattr(event, "message", None), "sender", None),
        ):
            title = normalize_entity_title(entity, fallback_id=None)
            if title != "unknown-chat":
                return title
        return normalize_entity_title(None, fallback_id=fallback_id)
    for entity in (
        getattr(event, "chat", None),
        getattr(getattr(event, "message", None), "chat", None),
    ):
        title = normalize_entity_title(entity, fallback_id=None)
        if title != "unknown-chat":
            return title
    return normalize_entity_title(None, fallback_id=fallback_id)


def build_event_chat_match_text(event: object) -> str:
    """Build matcher text for one inbound event, including username when available."""

    fallback_id = getattr(event, "chat_id", None)
    if bool(getattr(event, "is_private", False)):
        for entity in (
            getattr(event, "chat", None),
            getattr(event, "sender", None),
            getattr(getattr(event, "message", None), "chat", None),
            getattr(getattr(event, "message", None), "sender", None),
        ):
            match_text = build_entity_match_text(entity, fallback_id=None)
            if match_text != "unknown-chat":
                return match_text
        return build_entity_match_text(None, fallback_id=fallback_id)
    for entity in (
        getattr(event, "chat", None),
        getattr(getattr(event, "message", None), "chat", None),
    ):
        match_text = build_entity_match_text(entity, fallback_id=None)
        if match_text != "unknown-chat":
            return match_text
    return build_entity_match_text(None, fallback_id=fallback_id)


def normalize_entity_title(entity: object | None, *, fallback_id: object | None) -> str:
    """Render one Telethon user/chat-like entity into a human-readable title."""

    if entity is not None:
        for candidate in (
            getattr(entity, "name", None),
            getattr(entity, "title", None),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        first_name = getattr(entity, "first_name", None)
        last_name = getattr(entity, "last_name", None)
        display_parts = [
            str(part).strip()
            for part in (first_name, last_name)
            if isinstance(part, str) and part.strip()
        ]
        if display_parts:
            return " ".join(display_parts)
        username = getattr(entity, "username", None)
        if isinstance(username, str) and username.strip():
            return f"@{username.strip()}"
    if fallback_id is not None and str(fallback_id).strip():
        return str(fallback_id).strip()
    return "unknown-chat"


def build_entity_match_text(entity: object | None, *, fallback_id: object | None) -> str:
    """Build matcher text from one entity so filters can match both display names and usernames."""

    parts: list[str] = []
    if entity is not None:
        for candidate in (
            getattr(entity, "name", None),
            getattr(entity, "title", None),
        ):
            if isinstance(candidate, str) and candidate.strip():
                parts.append(candidate.strip())
        first_name = getattr(entity, "first_name", None)
        last_name = getattr(entity, "last_name", None)
        display_parts = [
            str(part).strip()
            for part in (first_name, last_name)
            if isinstance(part, str) and part.strip()
        ]
        if display_parts:
            parts.append(" ".join(display_parts))
        username = getattr(entity, "username", None)
        if isinstance(username, str) and username.strip():
            parts.append(f"@{username.strip()}")
            parts.append(username.strip())
    if fallback_id is not None and str(fallback_id).strip():
        parts.append(str(fallback_id).strip())
    return _join_match_parts(parts)


def normalize_chat_id(dialog: object) -> str | None:
    """Extract one stable chat id from a Telethon dialog-like object."""

    dialog_id = getattr(dialog, "id", None)
    if dialog_id is None:
        dialog_id = getattr(getattr(dialog, "entity", None), "id", None)
    if dialog_id is None:
        return None
    return str(dialog_id)


def matches_chat_title_filters(
    *,
    title: str,
    blocked_patterns: tuple[str, ...],
    allowed_patterns: tuple[str, ...],
) -> bool:
    """Apply simple case-insensitive substring allow/block filters to one chat title."""

    normalized_title = title.strip().lower()
    if any(pattern in normalized_title for pattern in blocked_patterns):
        return False
    if allowed_patterns and not any(pattern in normalized_title for pattern in allowed_patterns):
        return False
    return True


def _join_match_parts(parts: list[str]) -> str:
    """Join unique matcher fragments while preserving input order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for item in parts:
        candidate = item.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(candidate)
    if not normalized:
        return "unknown-chat"
    return " ".join(normalized)


def clip_watched_text(*, text: str, max_chars: int) -> str:
    """Collapse and clip one watched message body for digest batching."""

    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def render_watcher_batch_message(
    *,
    account_id: str,
    events: tuple[TelethonWatchedEvent, ...],
) -> str:
    """Render one batch of watched events into the turn input message."""

    collected_at = datetime.now(UTC).isoformat()
    dialog_count = len({item.chat_id for item in events})
    parts = [
        "Telegram watcher batch.",
        f"account_id: {account_id}",
        f"collected_at: {collected_at}",
        f"dialog_count: {dialog_count}",
        f"event_count: {len(events)}",
        "",
        "Events:",
    ]
    for index, item in enumerate(events, start=1):
        parts.extend(
            [
                f"{index}. [{item.chat_kind}] {item.chat_title}",
                f"chat_id: {item.chat_id}",
                f"message_id: {item.message_id}",
                f"observed_at: {item.observed_at}",
                f"sender_id: {item.sender_id or '-'}",
                "text:",
                item.text,
                "",
            ]
        )
    return "\n".join(parts).strip()


def build_watcher_context_overrides(
    *,
    endpoint_id: str,
    account_id: str,
    events: tuple[TelethonWatchedEvent, ...],
    delivery_target: ChannelDeliveryTarget,
) -> TurnContextOverrides:
    """Build runtime metadata and prompt overlay for one watcher digest turn."""

    runtime_metadata: dict[str, object] = {
        "transport": "telegram_user",
        "account_id": account_id,
        "peer_id": watcher_memory_peer_id(endpoint_id),
        "telethon_watcher": {
            "endpoint_id": endpoint_id,
            "account_id": account_id,
            "event_count": len(events),
            "dialog_count": len({item.chat_id for item in events}),
            "dialogs": tuple(sorted({item.chat_title for item in events})),
        }
    }
    serialized_delivery_target = build_delivery_target_runtime_metadata(delivery_target)
    if serialized_delivery_target is not None:
        runtime_metadata["delivery_target"] = serialized_delivery_target
    return TurnContextOverrides(
        runtime_metadata=runtime_metadata,
        prompt_overlay=_build_watcher_prompt_overlay(),
    )


def watcher_memory_peer_id(endpoint_id: str) -> str:
    """Return one synthetic local chat id used to isolate watcher memory from ordinary chats."""

    normalized = endpoint_id.strip()
    return f"{WATCHER_MEMORY_PEER_PREFIX}{normalized}"


def resolve_watcher_delivery_target(
    *,
    account_id: str,
    config: TelethonWatcherConfig,
) -> ChannelDeliveryTarget:
    """Resolve the watcher sink, defaulting to the connected account's Saved Messages."""

    if config.delivery_target is not None:
        if config.delivery_target.transport != "telegram_user":
            return config.delivery_target
        return ChannelDeliveryTarget(
            transport="telegram_user",
            account_id=config.delivery_target.account_id or account_id,
            peer_id=config.delivery_target.peer_id or "me",
            thread_id=config.delivery_target.thread_id,
            user_id=config.delivery_target.user_id,
        )
    return ChannelDeliveryTarget(
        transport="telegram_user",
        account_id=account_id,
        peer_id="me",
    )


def watcher_requires_live_sender(
    *,
    account_id: str,
    config: TelethonWatcherConfig,
) -> bool:
    """Return true when watcher delivery depends on the live Telethon client sender."""

    target = resolve_watcher_delivery_target(account_id=account_id, config=config)
    return target.transport == "telegram_user" and target.account_id == account_id


def is_no_digest_response(text: str) -> bool:
    """Return true when a watcher turn intentionally suppressed external delivery."""

    return text.strip() == WATCHER_NO_DIGEST_SENTINEL


def _build_watcher_prompt_overlay() -> str:
    return "\n".join(
        [
            "Telegram watched-dialog digest mode.",
            "Treat the incoming user message as a batch of collected Telegram activity, not as a direct user request.",
            "Use the active profile bootstrap instructions as your standing behavior.",
            "Select only items genuinely worth notifying the operator about.",
            "If nothing in the batch seems worth notifying, reply with EXACTLY `NO_DIGEST` and nothing else.",
            "If there are relevant items, produce a concise digest that names the chat and explains why each item matters.",
        ]
    )


def dialog_debug_payload(dialog: TelethonWatchedDialog) -> dict[str, Any]:
    """Return one lightweight debug payload for watcher diagnostics/tests."""

    return {
        "chat_id": dialog.chat_id,
        "chat_kind": dialog.chat_kind,
        "title": dialog.title,
    }
