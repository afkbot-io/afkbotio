"""Helpers for composing automation AgentLoop messages and session ids."""

from __future__ import annotations

import json
from collections.abc import Mapping


def webhook_session_id(*, automation_id: int, event_hash: str) -> str:
    """Build deterministic per-event session id for webhook execution isolation."""

    return f"automation-webhook-{automation_id}-{event_hash[:16]}"


def cron_session_id(*, automation_id: int, claim_token: str) -> str:
    """Build per-claim session id for cron execution isolation."""

    return f"automation-cron-{automation_id}-{claim_token[:16]}"


def compose_webhook_message(
    prompt: str,
    payload: Mapping[str, object],
) -> str:
    """Compose one webhook trigger message for AgentLoop."""

    normalized_prompt = prompt.strip()
    if not payload:
        return normalized_prompt
    serialized = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True)
    return f"{normalized_prompt}\n\nwebhook_payload={serialized}"


def compose_cron_message(prompt: str) -> str:
    """Compose one cron trigger message for AgentLoop."""

    return prompt.strip()
