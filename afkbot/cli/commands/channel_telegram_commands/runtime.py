"""Dependency contracts for Telegram channel CLI command execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from afkbot.services.channels.endpoint_contracts import TelegramPollingEndpointConfig
from afkbot.services.profile_runtime import ProfileDetails
from afkbot.settings import Settings

type TelegramPayload = dict[str, object]


@dataclass(frozen=True)
class TelegramCommandRuntime:
    """Runtime dependencies resolved by the facade for each Telegram CLI call."""

    settings: Settings
    create_endpoint: Callable[[TelegramPollingEndpointConfig], TelegramPollingEndpointConfig]
    update_endpoint: Callable[[TelegramPollingEndpointConfig], TelegramPollingEndpointConfig]
    list_endpoints: Callable[[], list[TelegramPollingEndpointConfig]]
    load_endpoint: Callable[[str], TelegramPollingEndpointConfig]
    delete_endpoint: Callable[[str], bool]
    delete_binding: Callable[[str], bool]
    load_profile: Callable[[str], ProfileDetails]
    state_path_for: Callable[[str], Path]
    set_endpoint_enabled: Callable[[str, bool], None]
    status_payload: Callable[[str | None, bool], TelegramPayload]
    render_status_payload: Callable[[TelegramPayload], None]
    poll_once_payload: Callable[[str], TelegramPayload]
    render_poll_once_payload: Callable[[str, TelegramPayload], None]
    reset_offset_payload: Callable[[str], TelegramPayload]
    raise_error: Callable[[Exception], None]
    reload_notice: Callable[[Settings], None]


__all__ = ["TelegramCommandRuntime", "TelegramPayload"]
