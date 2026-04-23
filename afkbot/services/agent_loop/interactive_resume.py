"""Shared helpers for interactive runtime resume flows."""

from __future__ import annotations

from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.pending_envelopes import (
    PROFILE_SELECTION_QUESTION_KIND,
    TOOL_NOT_ALLOWED_QUESTION_KIND,
)
from afkbot.services.agent_loop.safety_policy import CONFIRM_ACK_PARAM, CONFIRM_QID_PARAM
from afkbot.services.tools.base import ToolCall


def is_credential_profile_question(envelope: ActionEnvelope) -> bool:
    """Return true when one ask-question envelope expects credential profile choice."""

    patch = envelope.spec_patch or {}
    question_kind = str(patch.get("question_kind") or "").strip().lower()
    return question_kind == PROFILE_SELECTION_QUESTION_KIND


def is_tool_not_allowed_question(envelope: ActionEnvelope) -> bool:
    """Return true when one ask-question envelope asks to run non-visible tool."""

    patch = envelope.spec_patch or {}
    question_kind = str(patch.get("question_kind") or "").strip().lower()
    return question_kind == TOOL_NOT_ALLOWED_QUESTION_KIND


def available_profile_choices(envelope: ActionEnvelope) -> list[str]:
    """Extract normalized credential profile keys from one envelope payload."""

    patch = envelope.spec_patch or {}
    raw_profiles = patch.get("available_profile_keys")
    if not isinstance(raw_profiles, list):
        return []
    seen: set[str] = set()
    items: list[str] = []
    for raw_item in raw_profiles:
        item = str(raw_item or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def extract_resume_tool_call(envelope: ActionEnvelope) -> ToolCall | None:
    """Return deterministic replay tool call from one pending envelope."""

    patch = envelope.spec_patch or {}
    tool_name = str(patch.get("tool_name") or "").strip()
    if not tool_name:
        return None
    raw_params = patch.get("tool_params")
    if not isinstance(raw_params, dict):
        raw_params = {}
    params = {str(key): value for key, value in raw_params.items()}
    call_id = str(patch.get("tool_call_id") or "").strip() or None
    return ToolCall(name=tool_name, params=params, call_id=call_id)


def apply_profile_name_to_resume_call(*, tool_call: ToolCall, profile_name: str) -> ToolCall:
    """Inject selected credential profile into replay-safe tool call payload."""

    normalized_profile = profile_name.strip()
    if not normalized_profile:
        return tool_call
    if tool_call.name not in {
        "app.run",
        "credentials.request",
        "credentials.create",
        "credentials.update",
    }:
        return tool_call
    params = dict(tool_call.params)
    params["profile_name"] = normalized_profile
    return ToolCall(name=tool_call.name, params=params, call_id=tool_call.call_id)


def apply_approval_to_resume_call(
    *,
    tool_call: ToolCall,
    question_id: str | None,
) -> ToolCall:
    """Inject explicit confirmation markers into replay-safe approval call."""

    params = dict(tool_call.params)
    params[CONFIRM_ACK_PARAM] = True
    normalized_question_id = str(question_id or "").strip()
    if normalized_question_id:
        params[CONFIRM_QID_PARAM] = normalized_question_id
    return ToolCall(name=tool_call.name, params=params, call_id=tool_call.call_id)


def build_secure_resume_message(envelope: ActionEnvelope) -> str:
    """Build fallback synthetic resume message after secure credential capture."""

    patch = envelope.spec_patch or {}
    integration = str(patch.get("integration_name") or "integration").strip() or "integration"
    profile = str(patch.get("credential_profile_key") or "default").strip() or "default"
    field_name = (
        str(patch.get("credential_name") or envelope.secure_field or "credential").strip()
        or "credential"
    )
    return (
        "secure_resume: a required credential was captured via secure input. "
        f"integration={integration}; profile={profile}; field={field_name}. "
        "Do not ask for the same credential again. Continue the original task from the latest state "
        "and call the next tool directly if execution still requires it."
    )
