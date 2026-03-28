"""Compatibility facade for Telegram channel CLI registration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import typer

from afkbot.cli.commands.channel_telegram_commands import (
    TelegramCommandRuntime,
    register_telegram_command_tree,
)
from afkbot.cli.commands.channel_telegram_runtime import (
    load_telegram_endpoint,
    raise_channel_error,
    render_poll_once_payload,
    render_telegram_status_payload,
    set_endpoint_enabled,
    telegram_poll_once_payload,
    telegram_reset_offset_payload,
    telegram_status_payload,
)
from afkbot.cli.managed_runtime import reload_install_managed_runtime_notice
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    run_channel_binding_service_sync,
)
from afkbot.services.channels.endpoint_contracts import TelegramPollingEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    get_channel_endpoint_service,
    telegram_polling_state_path_for,
)
from afkbot.services.profile_runtime import ProfileDetails, run_profile_service_sync
from afkbot.settings import Settings, get_settings

TEndpointResult = TypeVar("TEndpointResult")

__all__ = [
    "get_channel_endpoint_service",
    "load_telegram_endpoint",
    "raise_channel_error",
    "register_telegram_commands",
    "reload_install_managed_runtime_notice",
]


def register_telegram_commands(channel_app: typer.Typer) -> None:
    """Register Telegram Bot API polling commands under `afk channel telegram`."""

    register_telegram_command_tree(
        channel_app,
        runtime_factory=_build_telegram_command_runtime,
    )


def _build_telegram_command_runtime() -> TelegramCommandRuntime:
    """Build one invocation-scoped runtime from current facade symbols."""

    settings = get_settings()
    return TelegramCommandRuntime(
        settings=settings,
        create_endpoint=lambda endpoint: _create_endpoint(settings, endpoint),
        update_endpoint=lambda endpoint: _update_endpoint(settings, endpoint),
        list_endpoints=lambda: _list_endpoints(settings),
        load_endpoint=lambda channel_id: asyncio.run(load_telegram_endpoint(channel_id=channel_id)),
        delete_endpoint=lambda channel_id: _delete_endpoint(settings, channel_id),
        delete_binding=lambda channel_id: _delete_binding(settings, channel_id),
        load_profile=lambda profile_id: _load_profile(settings, profile_id),
        state_path_for=lambda channel_id: telegram_polling_state_path_for(settings, endpoint_id=channel_id),
        set_endpoint_enabled=lambda channel_id, enabled: set_endpoint_enabled(
            channel_id=channel_id,
            enabled=enabled,
        ),
        status_payload=lambda channel_id, probe: asyncio.run(
            telegram_status_payload(channel_id=channel_id, probe=probe)
        ),
        render_status_payload=render_telegram_status_payload,
        poll_once_payload=lambda channel_id: asyncio.run(telegram_poll_once_payload(channel_id=channel_id)),
        render_poll_once_payload=lambda channel_id, payload: render_poll_once_payload(
            channel_id=channel_id,
            payload=payload,
        ),
        reset_offset_payload=lambda channel_id: asyncio.run(
            telegram_reset_offset_payload(channel_id=channel_id)
        ),
        raise_error=raise_channel_error,
        reload_notice=reload_install_managed_runtime_notice,
    )


def _create_endpoint(
    settings: Settings,
    endpoint: TelegramPollingEndpointConfig,
) -> TelegramPollingEndpointConfig:
    """Create and re-type one Telegram endpoint through the endpoint service."""

    created = _run_endpoint_service_sync(settings, lambda service: service.create(endpoint))
    return TelegramPollingEndpointConfig.model_validate(created.model_dump())


def _update_endpoint(
    settings: Settings,
    endpoint: TelegramPollingEndpointConfig,
) -> TelegramPollingEndpointConfig:
    """Update and re-type one Telegram endpoint through the endpoint service."""

    updated = _run_endpoint_service_sync(settings, lambda service: service.update(endpoint))
    return TelegramPollingEndpointConfig.model_validate(updated.model_dump())


def _list_endpoints(settings: Settings) -> list[TelegramPollingEndpointConfig]:
    """List Telegram endpoints through the endpoint service."""

    channels = _run_endpoint_service_sync(
        settings,
        lambda service: service.list(transport="telegram"),
    )
    return [TelegramPollingEndpointConfig.model_validate(item.model_dump()) for item in channels]


def _delete_endpoint(settings: Settings, channel_id: str) -> bool:
    """Delete one Telegram endpoint through the current endpoint-service facade."""

    return _run_endpoint_service_sync(
        settings,
        lambda service: service.delete(endpoint_id=channel_id),
    )


def _delete_binding(settings: Settings, channel_id: str) -> bool:
    """Delete one matching binding when it exists."""

    try:
        run_channel_binding_service_sync(
            settings,
            lambda service: service.delete(binding_id=channel_id),
        )
    except ChannelBindingServiceError:
        return False
    return True


def _load_profile(settings: Settings, profile_id: str) -> ProfileDetails:
    """Load one profile for Telegram inspection views."""

    return run_profile_service_sync(settings, lambda service: service.get(profile_id=profile_id))


def _run_endpoint_service_sync(
    settings: Settings,
    op: Callable[[ChannelEndpointService], Awaitable[TEndpointResult]],
) -> TEndpointResult:
    """Run one endpoint-service operation while preserving facade monkeypatch compatibility."""

    async def _run() -> TEndpointResult:
        service = get_channel_endpoint_service(settings)
        try:
            return await op(service)
        finally:
            shutdown = getattr(service, "shutdown", None)
            if callable(shutdown):
                await shutdown()

    return asyncio.run(_run())
