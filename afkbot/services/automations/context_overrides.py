"""Turn-level context overrides for automation-triggered executions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides

AutomationTriggerType = Literal["cron", "webhook"]


def build_automation_context_overrides(
    *,
    automation_id: int,
    trigger_type: AutomationTriggerType,
    event_hash: str | None = None,
    cron_expr: str | None = None,
    payload: Mapping[str, object] | None = None,
) -> TurnContextOverrides:
    """Build trusted prompt and runtime context for automation turns."""

    from afkbot.services.agent_loop.turn_context import TurnContextOverrides

    return TurnContextOverrides(
        runtime_metadata=_build_automation_runtime_metadata(
            automation_id=automation_id,
            trigger_type=trigger_type,
            event_hash=event_hash,
            cron_expr=cron_expr,
            payload=payload,
        ),
        trusted_runtime_context=_build_automation_trusted_runtime_context(
            automation_id=automation_id,
        ),
        prompt_overlay=_build_automation_prompt_overlay(
            automation_id=automation_id,
            trigger_type=trigger_type,
        ),
    )


def _build_automation_runtime_metadata(
    *,
    automation_id: int,
    trigger_type: AutomationTriggerType,
    event_hash: str | None,
    cron_expr: str | None,
    payload: Mapping[str, object] | None,
) -> dict[str, object]:
    payload_keys = tuple(sorted(str(key) for key in (payload or {}).keys()))
    automation_payload: dict[str, object] = {
        "automation_id": automation_id,
        "trigger_type": trigger_type,
    }
    if event_hash is not None:
        automation_payload["event_hash"] = event_hash
    if cron_expr is not None:
        automation_payload["cron_expr"] = cron_expr
    if payload_keys:
        automation_payload["payload_keys"] = payload_keys
    return {"automation": automation_payload}


def _build_automation_trusted_runtime_context(*, automation_id: int) -> dict[str, object]:
    return {"automation_runtime": {"automation_id": automation_id}}


def _build_automation_prompt_overlay(
    *,
    automation_id: int,
    trigger_type: AutomationTriggerType,
) -> str:
    parts = [
        "Automation execution context.",
        f"- automation_id: {automation_id}",
        f"- trigger_type: {trigger_type}",
        "Treat the incoming user message as the automation task prompt.",
        "Interpret the automation prompt as a self-contained task statement: what to do, expected result, and output constraints.",
        "When the prompt asks you to create Task Flow work, use task.create directly: include the target flow_id when known, assign owner_type=employee and the intended owner_ref, and do not set session_id or session_profile_id. The runtime will attribute the task to this automation actor.",
        "When creating work for a root or manager employee such as cto, create an intake/delegation task instead of an implementation task: add a manager-intake style label, describe the need to inspect context and delegate focused child tasks, and leave specialist execution to employee-owned child tasks.",
        "For merge-request or code-review webhooks, the manager task should coordinate review by delegating architecture, code, QA, or release checks; it should not instruct the manager to perform the whole review personally.",
        "Never impersonate a human or employee actor from an automation prompt; create the task as the automation and let Task Flow route it to the requested employee.",
    ]
    return "\n".join(parts)
