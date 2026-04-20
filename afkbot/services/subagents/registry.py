"""Per-root service registry for persisted subagent runtime."""

from __future__ import annotations

from afkbot.services.subagents.service import SubagentService
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, SubagentService] = {}


def get_subagent_service(settings: Settings) -> SubagentService:
    """Get or create one subagent service instance for current root directory."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = SubagentService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_subagent_services() -> None:
    """Reset cached service instances (used by tests)."""

    _SERVICES_BY_ROOT.clear()


async def reset_subagent_service_for_root_async(*, settings: Settings) -> None:
    """Reset cached service instance for one root and dispose its resources."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.pop(key, None)
    if service is None:
        return
    await service.shutdown()


async def reset_subagent_services_async() -> None:
    """Reset cached services and dispose their DB engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
