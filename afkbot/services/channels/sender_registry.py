"""Registry of live channel-owned outbound senders."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from afkbot.services.channels.delivery_runtime import ResolvedDeliveryTarget
from afkbot.settings import Settings

ChannelSenderFn = Callable[[ResolvedDeliveryTarget, str], Awaitable[dict[str, object]]]
_REGISTRIES_BY_ROOT: dict[str, "ChannelSenderRegistry"] = {}


class ChannelSenderRegistryError(ValueError):
    """Structured sender registry failure."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _SenderRecord:
    transport: str
    account_id: str
    sender: ChannelSenderFn


class ChannelSenderRegistry:
    """Process-local registry of live sender callbacks per transport/account."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[tuple[str, str], _SenderRecord] = {}

    async def register(
        self,
        *,
        transport: str,
        account_id: str,
        sender: ChannelSenderFn,
    ) -> None:
        """Register one live sender callback for transport/account."""

        key = (_normalize_transport(transport), _normalize_account_id(account_id))
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing.sender is not sender:
                raise ChannelSenderRegistryError(
                    error_code="channel_sender_conflict",
                    reason=(
                        f"Sender already registered for transport={key[0]} account_id={key[1]}"
                    ),
                )
            self._records[key] = _SenderRecord(
                transport=key[0],
                account_id=key[1],
                sender=sender,
            )

    async def unregister(
        self,
        *,
        transport: str,
        account_id: str,
        sender: ChannelSenderFn | None = None,
    ) -> None:
        """Remove one live sender callback when present."""

        key = (_normalize_transport(transport), _normalize_account_id(account_id))
        async with self._lock:
            existing = self._records.get(key)
            if existing is None:
                return
            if sender is not None and existing.sender is not sender:
                return
            self._records.pop(key, None)

    async def get_sender(
        self,
        *,
        transport: str,
        account_id: str,
    ) -> ChannelSenderFn:
        """Resolve one sender callback by transport/account or raise."""

        key = (_normalize_transport(transport), _normalize_account_id(account_id))
        async with self._lock:
            existing = self._records.get(key)
        if existing is None:
            raise ChannelSenderRegistryError(
                error_code="channel_sender_not_available",
                reason=f"No live sender for transport={key[0]} account_id={key[1]}",
            )
        return existing.sender


def get_channel_sender_registry(settings: Settings) -> ChannelSenderRegistry:
    """Return cached sender registry for one runtime root."""

    key = str(settings.root_dir.resolve())
    registry = _REGISTRIES_BY_ROOT.get(key)
    if registry is None:
        registry = ChannelSenderRegistry()
        _REGISTRIES_BY_ROOT[key] = registry
    return registry


def reset_channel_sender_registries() -> None:
    """Reset cached sender registries for tests."""

    _REGISTRIES_BY_ROOT.clear()


def _normalize_transport(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ChannelSenderRegistryError(
            error_code="channel_sender_transport_required",
            reason="Sender transport is required",
        )
    return normalized


def _normalize_account_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ChannelSenderRegistryError(
            error_code="channel_sender_account_required",
            reason="Sender account_id is required",
        )
    return normalized
