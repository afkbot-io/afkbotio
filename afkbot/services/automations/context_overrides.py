"""Turn-level context overrides for automation-triggered executions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides
    from afkbot.services.automations.contracts import AutomationDeliveryMode
    from afkbot.services.channels import ChannelDeliveryTarget

AutomationTriggerType = Literal["cron", "webhook"]


def build_automation_context_overrides(
    *,
    automation_id: int,
    trigger_type: AutomationTriggerType,
    subagent_markdown: str | None,
    event_hash: str | None = None,
    cron_expr: str | None = None,
    payload: Mapping[str, object] | None = None,
    delivery_mode: AutomationDeliveryMode = "tool",
    delivery_target: ChannelDeliveryTarget | None = None,
) -> TurnContextOverrides:
    """Build trusted prompt overlay plus untrusted metadata for automation turns."""

    from afkbot.services.agent_loop.turn_context import TurnContextOverrides

    return TurnContextOverrides(
        runtime_metadata=_build_automation_runtime_metadata(
            automation_id=automation_id,
            trigger_type=trigger_type,
            event_hash=event_hash,
            cron_expr=cron_expr,
            payload=payload,
            delivery_mode=delivery_mode,
            delivery_target=delivery_target,
        ),
        prompt_overlay=_build_automation_prompt_overlay(
            automation_id=automation_id,
            trigger_type=trigger_type,
            subagent_markdown=subagent_markdown,
            delivery_mode=delivery_mode,
            delivery_target=delivery_target,
        ),
    )


def _build_automation_runtime_metadata(
    *,
    automation_id: int,
    trigger_type: AutomationTriggerType,
    event_hash: str | None,
    cron_expr: str | None,
    payload: Mapping[str, object] | None,
    delivery_mode: AutomationDeliveryMode,
    delivery_target: ChannelDeliveryTarget | None,
) -> dict[str, object]:
    from afkbot.services.channels import build_delivery_target_runtime_metadata

    payload_keys = tuple(sorted(str(key) for key in (payload or {}).keys()))
    automation_payload: dict[str, object] = {
        "automation_id": automation_id,
        "trigger_type": trigger_type,
        "delivery_mode": delivery_mode,
    }
    if event_hash is not None:
        automation_payload["event_hash"] = event_hash
    if cron_expr is not None:
        automation_payload["cron_expr"] = cron_expr
    if payload_keys:
        automation_payload["payload_keys"] = payload_keys
    metadata: dict[str, object] = {"automation": automation_payload}
    serialized_delivery_target = build_delivery_target_runtime_metadata(delivery_target)
    if serialized_delivery_target is not None:
        metadata["delivery_target"] = serialized_delivery_target
    return metadata


def _build_automation_prompt_overlay(
    *,
    automation_id: int,
    trigger_type: AutomationTriggerType,
    subagent_markdown: str | None,
    delivery_mode: AutomationDeliveryMode,
    delivery_target: ChannelDeliveryTarget | None,
) -> str:
    instructions = (subagent_markdown or "").strip()
    parts = [
        "Automation execution context.",
        f"- automation_id: {automation_id}",
        f"- trigger_type: {trigger_type}",
        f"- delivery_mode: {delivery_mode}",
        "Treat the incoming user message as the automation task prompt.",
        "Interpret the automation prompt as a self-contained task statement: what to do, expected result, and output constraints. The schedule and delivery configuration are already stored outside the prompt.",
    ]
    if delivery_mode == "target":
        parts.append(
            "Use the platform delivery target for the final outbound message. Do not call app.run only to deliver the final report unless the task explicitly needs extra side effects or multi-step delivery."
        )
    elif delivery_mode == "tool":
        parts.append(
            "This automation is tool-driven. Use app.run, http.request, bash.exec, or other available tools as needed to perform the requested side effects. Do not assume the platform will auto-deliver the final text."
        )
    else:
        parts.append(
            "No automatic outbound delivery is configured. Execute the task and return a concise status summary."
        )
    if delivery_target is not None:
        parts.append(
            "- configured_delivery_target: "
            + ", ".join(
                f"{key}={value}"
                for key, value in (
                    ("transport", delivery_target.transport),
                    ("binding_id", delivery_target.binding_id),
                    ("account_id", delivery_target.account_id),
                    ("peer_id", delivery_target.peer_id),
                    ("thread_id", delivery_target.thread_id),
                    ("user_id", delivery_target.user_id),
                    ("address", delivery_target.address),
                )
                if value is not None
            )
        )
    if not instructions:
        return "\n".join(parts)
    parts.extend(["", "Trigger instructions:", instructions])
    return "\n".join(parts)
