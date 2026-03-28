"""Serialization helpers for persisted automation delivery defaults."""

from __future__ import annotations

import json

from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.channels import ChannelDeliveryTarget


def encode_delivery_target(target: ChannelDeliveryTarget | None) -> str | None:
    """Serialize optional delivery target into stable JSON for storage."""

    if target is None:
        return None
    return json.dumps(target.model_dump(exclude_none=True), ensure_ascii=True, sort_keys=True)


def decode_delivery_target(raw_value: str | None) -> ChannelDeliveryTarget | None:
    """Decode one persisted delivery target JSON payload."""

    if raw_value is None or not raw_value.strip():
        return None
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise AutomationsServiceError(
            error_code="invalid_delivery_target",
            reason="Persisted automation delivery target is not valid JSON",
        ) from exc
    try:
        return ChannelDeliveryTarget.model_validate(payload)
    except Exception as exc:
        raise AutomationsServiceError(
            error_code="invalid_delivery_target",
            reason="Persisted automation delivery target is invalid",
        ) from exc
