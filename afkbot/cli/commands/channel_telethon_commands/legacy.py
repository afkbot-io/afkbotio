"""Compatibility lookups for legacy Telethon CLI monkeypatch paths."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import TypeVar, cast

from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    get_channel_endpoint_service,
    run_channel_endpoint_service_sync,
)
from afkbot.settings import Settings

TEndpointValue = TypeVar("TEndpointValue")


def _legacy_module() -> ModuleType:
    """Load the compatibility facade lazily to avoid import cycles."""

    from afkbot.cli.commands import channel_telethon as legacy

    return legacy


async def load_legacy_telethon_endpoint(*, channel_id: str) -> TelethonUserEndpointConfig:
    """Resolve the facade-exported endpoint loader at call time."""

    return cast(
        TelethonUserEndpointConfig,
        await _legacy_module().load_telethon_endpoint(channel_id=channel_id),
    )


def get_legacy_channel_endpoint_service(settings: Settings) -> ChannelEndpointService:
    """Resolve the facade-exported endpoint service getter at call time."""

    return cast(ChannelEndpointService, _legacy_module().get_channel_endpoint_service(settings))


def run_legacy_channel_endpoint_service_sync(
    settings: Settings,
    op: Callable[[ChannelEndpointService], Awaitable[TEndpointValue]],
) -> TEndpointValue:
    """Run a facade-compatible endpoint operation and dispose owned DB resources."""

    legacy_getter = _legacy_module().get_channel_endpoint_service
    if legacy_getter is get_channel_endpoint_service:
        return run_channel_endpoint_service_sync(settings, op)

    async def _run() -> TEndpointValue:
        service = cast(ChannelEndpointService, legacy_getter(settings))
        try:
            return await op(service)
        finally:
            shutdown = getattr(service, "shutdown", None)
            if callable(shutdown):
                await shutdown()

    return asyncio.run(_run())


def raise_legacy_telethon_channel_error(exc: Exception) -> None:
    """Delegate CLI error mapping through the legacy facade."""

    _legacy_module().raise_telethon_channel_error(exc)


def reload_legacy_managed_runtime_notice(settings: Settings) -> None:
    """Delegate managed-runtime reload notices through the legacy facade."""

    _legacy_module().reload_install_managed_runtime_notice(settings)


__all__ = [
    "get_legacy_channel_endpoint_service",
    "load_legacy_telethon_endpoint",
    "raise_legacy_telethon_channel_error",
    "reload_legacy_managed_runtime_notice",
    "run_legacy_channel_endpoint_service_sync",
]
