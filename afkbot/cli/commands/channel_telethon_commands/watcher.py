"""Watcher-specific builders and render helpers for Telethon CLI commands."""

from __future__ import annotations

from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.services.channels.endpoint_contracts import (
    TelethonUserEndpointConfig,
    TelethonWatcherConfig,
)


def build_watcher_config(
    *,
    enabled: bool,
    unmuted_only: bool,
    include_private: bool,
    include_groups: bool,
    include_channels: bool,
    batch_interval_sec: int,
    dialog_refresh_interval_sec: int,
    max_batch_size: int,
    max_buffer_size: int,
    max_message_chars: int,
    blocked_chat_patterns: tuple[str, ...],
    allowed_chat_patterns: tuple[str, ...],
    delivery_transport: str | None,
    delivery_account_id: str | None,
    delivery_peer_id: str | None,
    delivery_credential_profile_key: str | None,
) -> TelethonWatcherConfig:
    """Build one Telethon watcher config from normalized CLI values."""

    delivery_target = build_watcher_delivery_target(
        transport=delivery_transport,
        account_id=delivery_account_id,
        peer_id=delivery_peer_id,
    )
    return TelethonWatcherConfig(
        enabled=enabled,
        unmuted_only=unmuted_only,
        include_private=include_private,
        include_groups=include_groups,
        include_channels=include_channels,
        batch_interval_sec=batch_interval_sec,
        dialog_refresh_interval_sec=dialog_refresh_interval_sec,
        max_batch_size=max_batch_size,
        max_buffer_size=max_buffer_size,
        max_message_chars=max_message_chars,
        blocked_chat_patterns=blocked_chat_patterns,
        allowed_chat_patterns=allowed_chat_patterns,
        delivery_target=delivery_target,
        delivery_credential_profile_key=delivery_credential_profile_key,
    )


def merge_watcher_config(
    *,
    current: TelethonWatcherConfig,
    enabled: bool | None = None,
    unmuted_only: bool | None = None,
    include_private: bool | None = None,
    include_groups: bool | None = None,
    include_channels: bool | None = None,
    batch_interval_sec: int | None = None,
    dialog_refresh_interval_sec: int | None = None,
    max_batch_size: int | None = None,
    max_buffer_size: int | None = None,
    max_message_chars: int | None = None,
    blocked_chat_patterns: tuple[str, ...] | None = None,
    allowed_chat_patterns: tuple[str, ...] | None = None,
    delivery_transport: str | None = None,
    delivery_account_id: str | None = None,
    delivery_peer_id: str | None = None,
    delivery_credential_profile_key: str | None = None,
) -> TelethonWatcherConfig:
    """Merge optional CLI overrides into one watcher config."""

    current_target = current.delivery_target
    return build_watcher_config(
        enabled=current.enabled if enabled is None else enabled,
        unmuted_only=current.unmuted_only if unmuted_only is None else unmuted_only,
        include_private=current.include_private if include_private is None else include_private,
        include_groups=current.include_groups if include_groups is None else include_groups,
        include_channels=current.include_channels if include_channels is None else include_channels,
        batch_interval_sec=current.batch_interval_sec if batch_interval_sec is None else batch_interval_sec,
        dialog_refresh_interval_sec=(
            current.dialog_refresh_interval_sec
            if dialog_refresh_interval_sec is None
            else dialog_refresh_interval_sec
        ),
        max_batch_size=current.max_batch_size if max_batch_size is None else max_batch_size,
        max_buffer_size=current.max_buffer_size if max_buffer_size is None else max_buffer_size,
        max_message_chars=current.max_message_chars if max_message_chars is None else max_message_chars,
        blocked_chat_patterns=current.blocked_chat_patterns if blocked_chat_patterns is None else blocked_chat_patterns,
        allowed_chat_patterns=current.allowed_chat_patterns if allowed_chat_patterns is None else allowed_chat_patterns,
        delivery_transport=(
            current_target.transport if delivery_transport is None and current_target is not None else delivery_transport
        ),
        delivery_account_id=(
            current_target.account_id if delivery_account_id is None and current_target is not None else delivery_account_id
        ),
        delivery_peer_id=(
            current_target.peer_id if delivery_peer_id is None and current_target is not None else delivery_peer_id
        ),
        delivery_credential_profile_key=(
            current.delivery_credential_profile_key
            if delivery_credential_profile_key is None
            else delivery_credential_profile_key
        ),
    )


def build_watcher_delivery_target(
    *,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
) -> ChannelDeliveryTarget | None:
    """Build optional explicit watcher delivery target from CLI flags."""

    normalized_transport = (transport or "").strip().lower()
    normalized_account_id = (account_id or "").strip() or None
    normalized_peer_id = (peer_id or "").strip() or None
    if not normalized_transport and normalized_account_id is None and normalized_peer_id is None:
        return None
    return ChannelDeliveryTarget(
        transport=normalized_transport or "telegram_user",
        account_id=normalized_account_id,
        peer_id=normalized_peer_id,
    )


def render_watcher_list_summary(channel: TelethonUserEndpointConfig) -> str:
    """Render compact watcher summary for `afk channel telethon list`."""

    watcher = channel.watcher
    if not watcher.enabled:
        return "off"
    source_kinds = [
        label
        for enabled, label in (
            (watcher.include_private, "private"),
            (watcher.include_groups, "groups"),
            (watcher.include_channels, "channels"),
        )
        if enabled
    ]
    target = watcher.delivery_target
    if target is None:
        target_text = "saved_messages"
    else:
        target_text = f"{target.transport}:{target.peer_id or '-'}"
    return (
        f"on(sources={'+'.join(source_kinds)}, batch={watcher.batch_interval_sec}s, "
        f"blocked={len(watcher.blocked_chat_patterns)}, target={target_text})"
    )


__all__ = [
    "build_watcher_config",
    "build_watcher_delivery_target",
    "merge_watcher_config",
    "render_watcher_list_summary",
]
