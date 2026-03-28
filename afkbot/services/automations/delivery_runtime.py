"""Shared best-effort outbound delivery helpers for automation executions."""

from __future__ import annotations

import logging

from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.service import ChannelDeliveryService

_LOGGER = logging.getLogger(__name__)


async def deliver_automation_turn_best_effort(
    *,
    delivery_service: ChannelDeliveryService,
    turn_result: object,
    delivery_target: ChannelDeliveryTarget,
    automation_id: int,
    trigger_type: str,
) -> None:
    """Attempt outbound delivery without replaying automation execution on failure."""

    try:
        await delivery_service.deliver_turn_result(
            turn_result=turn_result,
            target=delivery_target,
        )
    except Exception:
        _LOGGER.exception(
            "automation_outbound_delivery_failed automation_id=%s trigger_type=%s transport=%s",
            automation_id,
            trigger_type,
            delivery_target.transport,
        )
