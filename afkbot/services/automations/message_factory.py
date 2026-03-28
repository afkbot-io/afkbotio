"""Helpers for composing automation AgentLoop messages and session ids."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from afkbot.services.subagents.loader import SubagentLoader

_CRON_SUBAGENT_NAME = "cron"
_WEBHOOK_SUBAGENT_NAME = "webhook"


def webhook_session_id(*, automation_id: int, event_hash: str) -> str:
    """Build deterministic per-event session id for webhook execution isolation."""

    return f"automation-webhook-{automation_id}-{event_hash[:16]}"


def cron_session_id(*, automation_id: int, claim_token: str) -> str:
    """Build per-claim session id for cron execution isolation."""

    return f"automation-cron-{automation_id}-{claim_token[:16]}"


async def load_trigger_subagent_markdown(
    *,
    loader: SubagentLoader | None,
    profile_id: str,
    trigger_type: Literal["cron", "webhook"],
) -> str | None:
    """Load optional trigger-specific subagent markdown if present."""

    if loader is None:
        return None
    subagent_name = _CRON_SUBAGENT_NAME if trigger_type == "cron" else _WEBHOOK_SUBAGENT_NAME
    try:
        return await loader.load_subagent_markdown(
            name=subagent_name,
            profile_id=profile_id,
        )
    except FileNotFoundError:
        return None


def compose_webhook_message(
    prompt: str,
    payload: Mapping[str, object],
    *,
    subagent_markdown: str | None,
) -> str:
    """Compose one webhook trigger message for AgentLoop."""

    return compose_automation_message(
        trigger_type="webhook",
        prompt=prompt,
        subagent_markdown=subagent_markdown,
        payload=payload,
    )


def compose_cron_message(prompt: str, *, subagent_markdown: str | None) -> str:
    """Compose one cron trigger message for AgentLoop."""

    return compose_automation_message(
        trigger_type="cron",
        prompt=prompt,
        subagent_markdown=subagent_markdown,
        payload=None,
    )


def compose_automation_message(
    *,
    trigger_type: Literal["cron", "webhook"],
    prompt: str,
    subagent_markdown: str | None,
    payload: Mapping[str, object] | None,
) -> str:
    """Compose one AgentLoop message for a cron/webhook automation trigger."""

    normalized_prompt = prompt.strip()
    payload_part = ""
    if payload:
        serialized = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True)
        payload_part = f"\n\nwebhook_payload={serialized}"

    if subagent_markdown is None or not subagent_markdown.strip():
        return f"{normalized_prompt}{payload_part}"

    instructions = subagent_markdown.strip()
    return (
        f"automation_trigger={trigger_type}\n"
        f"automation_subagent={trigger_type}\n\n"
        f"subagent_instructions_md:\n{instructions}\n\n"
        f"automation_prompt:\n{normalized_prompt}{payload_part}"
    )
