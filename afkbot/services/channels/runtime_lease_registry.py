"""Registry preventing duplicate live channel runtimes per transport/account."""

from __future__ import annotations

import asyncio
import uuid

from afkbot.settings import Settings

_REGISTRIES_BY_ROOT: dict[str, "ChannelRuntimeLeaseRegistry"] = {}


class ChannelRuntimeLeaseRegistryError(ValueError):
    """Structured runtime lease conflict or validation failure."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class ChannelRuntimeLeaseRegistry:
    """Process-local lease map for active channel runtimes."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._leases: dict[tuple[str, str], str] = {}

    async def acquire(self, *, transport: str, account_id: str) -> str:
        """Acquire one live runtime lease or raise on duplicate."""

        key = (_normalize_transport(transport), _normalize_account_id(account_id))
        async with self._lock:
            if key in self._leases:
                raise ChannelRuntimeLeaseRegistryError(
                    error_code="channel_runtime_account_conflict",
                    reason=(
                        f"Runtime already active for transport={key[0]} account_id={key[1]}"
                    ),
                )
            owner_token = uuid.uuid4().hex
            self._leases[key] = owner_token
            return owner_token

    async def release(self, *, transport: str, account_id: str, owner_token: str) -> bool:
        """Release one live runtime lease when present."""

        key = (_normalize_transport(transport), _normalize_account_id(account_id))
        async with self._lock:
            if self._leases.get(key) != owner_token:
                return False
            del self._leases[key]
            return True


def get_channel_runtime_lease_registry(settings: Settings) -> ChannelRuntimeLeaseRegistry:
    """Return cached runtime lease registry for one runtime root."""

    key = str(settings.root_dir.resolve())
    registry = _REGISTRIES_BY_ROOT.get(key)
    if registry is None:
        registry = ChannelRuntimeLeaseRegistry()
        _REGISTRIES_BY_ROOT[key] = registry
    return registry


def reset_channel_runtime_lease_registries() -> None:
    """Reset cached runtime lease registries for tests."""

    _REGISTRIES_BY_ROOT.clear()


def _normalize_transport(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ChannelRuntimeLeaseRegistryError(
            error_code="channel_runtime_transport_required",
            reason="Runtime transport is required",
        )
    return normalized


def _normalize_account_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ChannelRuntimeLeaseRegistryError(
            error_code="channel_runtime_account_required",
            reason="Runtime account_id is required",
        )
    return normalized
