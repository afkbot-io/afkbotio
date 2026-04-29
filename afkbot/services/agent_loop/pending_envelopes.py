"""Builders for approval, profile-selection, and secure-field envelopes."""

from __future__ import annotations
from collections.abc import Callable
from uuid import uuid4

from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.safety_policy import (
    APPROVAL_REQUIRED_ERROR_CODE,
    CONFIRM_ACK_PARAM,
    CONFIRM_QID_PARAM,
)
from afkbot.services.tools.base import ToolCall, ToolResult

PROFILE_SELECTION_ERROR_CODE = "credential_profile_required"
PROFILE_SELECTION_QUESTION_KIND = "credential_profile_required"
TOOL_NOT_ALLOWED_QUESTION_KIND = "tool_not_allowed_in_turn"
SECURE_REQUEST_ERROR_CODES = frozenset(
    {
        "credentials_missing",
        "credential_binding_conflict",
        "security_secure_input_required",
    }
)
SECURE_REQUEST_NO_RESUME_ERROR_CODES = frozenset({"security_secure_input_required"})


class PendingEnvelopeBuilder:
    """Build action envelopes from tool failures that require user interaction."""

    def __init__(
        self,
        *,
        params_normalizer: Callable[[dict[str, object]], dict[str, object]],
    ) -> None:
        self._params_normalizer = params_normalizer

    def build_approval_envelope(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> ActionEnvelope | None:
        """Build ask-question envelope for approval-required tool failures."""

        for call, result in zip(tool_calls, tool_results, strict=True):
            error_code = (result.error_code or "").strip()
            if error_code != APPROVAL_REQUIRED_ERROR_CODE:
                continue
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            mode = str(metadata.get("approval_mode") or "strict").strip() or "strict"
            reason = str(result.reason or metadata.get("approval_reason") or "").strip()
            question_id = f"approval:{uuid4().hex}"
            message = (
                f"Safety confirmation required ({mode}): {reason} "
                "Approve execution with yes/no."
            )
            call_params = self._params_normalizer(call.params)
            call_params.pop(CONFIRM_ACK_PARAM, None)
            call_params.pop(CONFIRM_QID_PARAM, None)
            return ActionEnvelope(
                action="ask_question",
                message=message,
                question_id=question_id,
                spec_patch={
                    "tool_name": call.name,
                    "tool_params": call_params,
                    "tool_call_id": call.call_id,
                    "approval_mode": mode,
                    "approval_reason": reason,
                },
            )
        return None

    def build_profile_selection_envelope(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> ActionEnvelope | None:
        """Build ask-question envelope for explicit credential profile selection."""

        for call, result in zip(tool_calls, tool_results, strict=True):
            error_code = (result.error_code or "").strip()
            if error_code != PROFILE_SELECTION_ERROR_CODE:
                continue
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            available_profile_keys = self._normalize_profile_keys(
                metadata.get("available_profile_keys"),
            )
            if not available_profile_keys:
                continue
            call_params = self._params_normalizer(call.params)
            integration_name = str(
                metadata.get("integration_name")
                or call_params.get("integration_name")
                or call_params.get("app_name")
                or self._integration_from_tool_name(str(call_params.get("tool_name") or ""))
                or call.name.split(".", 1)[0]
                or "integration"
            ).strip()
            credential_name = str(
                metadata.get("credential_name")
                or call_params.get("credential_name")
                or call_params.get("credential_slug")
                or "credential"
            ).strip()
            question_id = f"profile:{uuid4().hex}"
            if len(available_profile_keys) == 1:
                requested_profile_key = str(
                    metadata.get("requested_profile_key")
                    or call_params.get("credential_profile_key")
                    or ""
                ).strip()
                available_profile = available_profile_keys[0]
                if requested_profile_key:
                    message = (
                        f"Credential profile '{requested_profile_key}' is unavailable for integration "
                        f"'{integration_name}' and credential '{credential_name}'. Choose available "
                        f"profile '{available_profile}' to continue."
                    )
                else:
                    message = (
                        f"Credential profile selection is required for integration '{integration_name}' "
                        f"and credential '{credential_name}'. Choose available profile "
                        f"'{available_profile}' to continue."
                    )
            else:
                joined_profiles = ", ".join(available_profile_keys)
                message = (
                    f"Multiple credential profiles are available for integration '{integration_name}' "
                    f"and credential '{credential_name}'. Choose one of: {joined_profiles}."
                )
            return ActionEnvelope(
                action="ask_question",
                message=message,
                question_id=question_id,
                spec_patch={
                    "question_kind": PROFILE_SELECTION_QUESTION_KIND,
                    "tool_name": call.name,
                    "tool_params": call_params,
                    "tool_call_id": call.call_id,
                    "integration_name": integration_name,
                    "credential_name": credential_name,
                    "available_profile_keys": list(available_profile_keys),
                    "error_code": error_code,
                },
            )
        return None

    def build_tool_not_allowed_envelope(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> ActionEnvelope | None:
        """Build ask-question envelope for tool-surface violations."""

        for call, result in zip(tool_calls, tool_results, strict=True):
            error_code = (result.error_code or "").strip()
            if error_code != TOOL_NOT_ALLOWED_QUESTION_KIND:
                continue
            tool_name = str(call.name).strip()
            normalized_call_params = self._params_normalizer(call.params)
            question_id = f"tool_not_allowed:{uuid4().hex}"
            message = "Tool access requires explicit approval before execution."
            return ActionEnvelope(
                action="ask_question",
                message=message,
                question_id=question_id,
                spec_patch={
                    "question_kind": TOOL_NOT_ALLOWED_QUESTION_KIND,
                    "tool_name": tool_name,
                    "tool_params": normalized_call_params,
                    "tool_call_id": call.call_id,
                    "tool_not_allowed_reason": str(
                        result.reason or "Requested tool is outside the visible surface."
                    ).strip(),
                    "error_code": error_code,
                },
            )
        return None

    def build_secure_envelope(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> ActionEnvelope | None:
        """Build secure-field request envelope from secret-input tool failures."""

        for call, result in zip(tool_calls, tool_results, strict=True):
            error_code = (result.error_code or "").strip()
            if error_code not in SECURE_REQUEST_ERROR_CODES:
                continue
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            call_params = self._params_normalizer(call.params)
            integration_name = str(
                metadata.get("integration_name")
                or call_params.get("integration_name")
                or call_params.get("app_name")
                or self._integration_from_tool_name(str(call_params.get("tool_name") or ""))
                or call.name.split(".", 1)[0]
                or "integration"
            ).strip()
            credential_name = str(
                metadata.get("credential_name")
                or call_params.get("credential_name")
                or call_params.get("credential_slug")
                or "credential"
            ).strip()
            credential_profile_key = str(
                metadata.get("credential_profile_key")
                or call_params.get("credential_profile_key")
                or call_params.get("profile_name")
                or "default"
            ).strip()
            secure_nonce = uuid4().hex
            question_id = f"secure:{uuid4().hex}"
            message = (
                f"Secure input is required for credential '{credential_name}' in integration "
                f"'{integration_name}' using credential profile '{credential_profile_key}'."
            )
            resume_tool_name = call.name
            resume_tool_params: dict[str, object] | None = call_params
            if error_code in SECURE_REQUEST_NO_RESUME_ERROR_CODES:
                resume_tool_name = ""
                resume_tool_params = None
            return ActionEnvelope(
                action="request_secure_field",
                message=message,
                question_id=question_id,
                secure_field=credential_name,
                spec_patch={
                    "tool_name": resume_tool_name,
                    "tool_params": resume_tool_params,
                    "tool_call_id": call.call_id,
                    "integration_name": integration_name,
                    "credential_name": credential_name,
                    "credential_profile_key": credential_profile_key,
                    "error_code": error_code,
                    "secure_nonce": secure_nonce,
                },
            )
        return None

    @staticmethod
    def _integration_from_tool_name(tool_name: str) -> str | None:
        """Extract integration prefix from one tool name."""

        normalized = tool_name.strip()
        if not normalized:
            return None
        prefix = normalized.split(".", 1)[0].strip()
        return prefix or None

    @staticmethod
    def _normalize_profile_keys(raw_value: object) -> tuple[str, ...]:
        """Return deterministic normalized profile key tuple from metadata payload."""

        if not isinstance(raw_value, (list, tuple, set)):
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            value = str(item).strip()
            if not value or value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return tuple(normalized)
