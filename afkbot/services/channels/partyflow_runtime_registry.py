"""Process-local registry of active PartyFlow webhook runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

from afkbot.settings import Settings

_REGISTRIES_BY_ROOT: dict[str, "PartyFlowWebhookRuntimeRegistry"] = {}


class PartyFlowWebhookRuntime(Protocol):
    """Minimal runtime contract needed by the FastAPI webhook route."""

    async def handle_webhook(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, object]]: ...


class PartyFlowWebhookRuntimeRegistry:
    """Map active PartyFlow endpoint ids to their live runtime services."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._services: dict[str, PartyFlowWebhookRuntime] = {}

    async def register(self, *, endpoint_id: str, service: PartyFlowWebhookRuntime) -> None:
        """Register one live PartyFlow runtime for webhook dispatch."""

        normalized = endpoint_id.strip().lower()
        if not normalized:
            raise ValueError("endpoint_id is required")
        async with self._lock:
            self._services[normalized] = service

    async def unregister(
        self,
        *,
        endpoint_id: str,
        service: PartyFlowWebhookRuntime | None = None,
    ) -> None:
        """Remove one live PartyFlow runtime when present."""

        normalized = endpoint_id.strip().lower()
        if not normalized:
            return
        async with self._lock:
            existing = self._services.get(normalized)
            if existing is None:
                return
            if service is not None and existing is not service:
                return
            self._services.pop(normalized, None)

    def get(self, endpoint_id: str) -> PartyFlowWebhookRuntime | None:
        """Return the registered runtime service for one endpoint id when present."""

        normalized = endpoint_id.strip().lower()
        if not normalized:
            return None
        return self._services.get(normalized)


def get_partyflow_webhook_runtime_registry(settings: Settings) -> PartyFlowWebhookRuntimeRegistry:
    """Return cached PartyFlow webhook runtime registry for one runtime root."""

    key = str(settings.root_dir.resolve())
    registry = _REGISTRIES_BY_ROOT.get(key)
    if registry is None:
        registry = PartyFlowWebhookRuntimeRegistry()
        _REGISTRIES_BY_ROOT[key] = registry
    return registry


def reset_partyflow_webhook_runtime_registries() -> None:
    """Reset cached PartyFlow webhook runtime registries for tests."""

    _REGISTRIES_BY_ROOT.clear()
