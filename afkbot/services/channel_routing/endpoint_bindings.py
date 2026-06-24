"""Endpoint-owned channel binding lifecycle helpers."""

from __future__ import annotations

from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.service import (
    ChannelBindingService,
    ChannelBindingServiceError,
)


def channel_binding_belongs_to_endpoint(*, binding_id: str, endpoint_id: str) -> bool:
    """Return whether one binding id is owned by one endpoint id."""

    normalized_binding_id = str(binding_id or "").strip()
    normalized_endpoint_id = str(endpoint_id or "").strip()
    if not normalized_binding_id or not normalized_endpoint_id:
        return False
    return normalized_binding_id == normalized_endpoint_id or normalized_binding_id.startswith(
        f"{normalized_endpoint_id}:"
    )


def count_endpoint_owned_bindings(
    *,
    bindings: list[ChannelBindingRule] | tuple[ChannelBindingRule, ...],
    endpoint_id: str,
) -> int:
    """Count enabled binding rules owned by one endpoint id."""

    return sum(
        1
        for item in bindings
        if item.enabled
        and channel_binding_belongs_to_endpoint(
            binding_id=item.binding_id,
            endpoint_id=endpoint_id,
        )
    )


async def set_endpoint_owned_bindings_enabled(
    *,
    service: ChannelBindingService,
    endpoint_id: str,
    transport: str,
    enabled: bool,
) -> int:
    """Enable or disable all binding rules owned by one endpoint."""

    updated_count = 0
    bindings = await service.list(transport=transport)
    for binding in bindings:
        if not channel_binding_belongs_to_endpoint(
            binding_id=binding.binding_id,
            endpoint_id=endpoint_id,
        ):
            continue
        await service.put(
            ChannelBindingRule(**(binding.model_dump(mode="python") | {"enabled": enabled}))
        )
        updated_count += 1
    return updated_count


async def delete_endpoint_owned_bindings(
    *,
    service: ChannelBindingService,
    endpoint_id: str,
    transport: str,
) -> int:
    """Delete all binding rules owned by one endpoint."""

    deleted_count = 0
    existing = await service.list(transport=transport)
    for rule in existing:
        if not channel_binding_belongs_to_endpoint(
            binding_id=rule.binding_id,
            endpoint_id=endpoint_id,
        ):
            continue
        try:
            await service.delete(binding_id=rule.binding_id)
            deleted_count += 1
        except ChannelBindingServiceError as exc:
            if exc.error_code != "channel_binding_not_found":
                raise
    return deleted_count
