"""Shared runtime target assembly for automation-triggered AgentLoop turns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

if TYPE_CHECKING:
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides
    from afkbot.services.automations.contracts import AutomationDeliveryMode
    from afkbot.services.channels import ChannelDeliveryTarget


@dataclass(frozen=True, slots=True)
class AutomationRuntimeTarget:
    """Effective target and trusted context overrides for one automation trigger."""

    profile_id: str
    session_id: str
    context_overrides: TurnContextOverrides | None


def build_automation_runtime_target(
    *,
    profile_id: str,
    session_id: str,
    automation_id: int,
    trigger_type: Literal["cron", "webhook"],
    subagent_markdown: str | None,
    event_hash: str | None = None,
    cron_expr: str | None = None,
    payload: Mapping[str, object] | None = None,
    delivery_mode: AutomationDeliveryMode = "tool",
    delivery_target: ChannelDeliveryTarget | None = None,
) -> AutomationRuntimeTarget:
    """Build one shared automation target using the same target/context layering as chat."""

    from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
    from afkbot.services.automations.context_overrides import build_automation_context_overrides
    from afkbot.services.channel_routing.runtime_target import (
        RuntimeTarget,
        build_routing_context_overrides,
    )

    target = RuntimeTarget(
        profile_id=profile_id,
        session_id=session_id,
        routing=None,
    )
    routing_overrides = build_routing_context_overrides(
        target=target,
        transport="automation",
        account_id=str(automation_id),
    )
    automation_overrides = build_automation_context_overrides(
        automation_id=automation_id,
        trigger_type=trigger_type,
        subagent_markdown=subagent_markdown,
        event_hash=event_hash,
        cron_expr=cron_expr,
        payload=payload,
        delivery_mode=delivery_mode,
        delivery_target=delivery_target,
    )
    return AutomationRuntimeTarget(
        profile_id=profile_id,
        session_id=session_id,
        context_overrides=merge_turn_context_overrides(routing_overrides, automation_overrides),
    )
