"""Shared reply suppression policy for human-facing channel transports."""

from __future__ import annotations

from afkbot.services.agent_loop.action_contracts import ActionEnvelope


def envelope_error_code(envelope: ActionEnvelope) -> str | None:
    """Extract one normalized error code from an action envelope when present."""

    patch = envelope.spec_patch or {}
    value = patch.get("error_code")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def should_suppress_channel_reply(envelope: ActionEnvelope) -> bool:
    """Hide transient/internal LLM failures from chat users."""

    error_code = envelope_error_code(envelope)
    return bool(error_code and error_code.startswith("llm_"))
