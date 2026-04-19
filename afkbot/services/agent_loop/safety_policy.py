"""Runtime safety and approval policy helpers."""

from __future__ import annotations

import re

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.tools.base import ToolResult

APPROVAL_REQUIRED_ERROR_CODE = "approval_required"
CONFIRM_ACK_PARAM = "_afkbot_confirmed"
CONFIRM_QID_PARAM = "_afkbot_question_id"

_MEDIUM_BASH_DELETE_RE = re.compile(
    r"(?<![a-z0-9_])(rm|rmdir|del|erase|unlink|truncate|shred)(?![a-z0-9_])",
    re.IGNORECASE,
)
_MEDIUM_DELETE_INTENT_RE = re.compile(
    r"(delete|remove|rm|rmdir|del|unlink|truncate|wipe|удал|стер|очист)",
    re.IGNORECASE,
)
_SHELL_PAYLOAD_PARAM_NAMES = frozenset({"cmd", "command", "chars"})
_STRICT_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "app.list",
        "automation.get",
        "automation.list",
        "credentials.list",
        "debug.echo",
        "file.list",
        "file.read",
        "file.search",
        "memory.search",
        "memory.recall.search",
        "mcp.tools.list",
        "skill.profile.get",
        "skill.profile.list",
        "subagent.profile.get",
        "subagent.profile.list",
        "subagent.result",
        "subagent.wait",
        "web.fetch",
        "web.search",
    }
)


class SafetyPolicy:
    """Encapsulate runtime approval requirements and safety prompt shaping."""

    @staticmethod
    def extract_confirmation_ack(params: dict[str, object]) -> tuple[bool, str | None]:
        """Extract and remove runtime confirmation markers from one tool params dict."""

        raw_ack = params.pop(CONFIRM_ACK_PARAM, False)
        raw_question_id = params.pop(CONFIRM_QID_PARAM, None)
        normalized_ack = str(raw_ack).strip().lower()
        confirmed = bool(raw_ack is True or normalized_ack in {"1", "true", "yes", "y"})
        question_id = str(raw_question_id or "").strip() or None
        return confirmed, question_id

    def approval_required_result(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
        confirmed: bool,
        question_id: str | None,
    ) -> ToolResult | None:
        """Return deterministic approval-required error when policy blocks execution."""

        mode, reason = self._approval_requirement(
            policy=policy,
            tool_name=tool_name,
            params=params,
            confirmed=confirmed,
        )
        if mode is None:
            return None
        metadata: dict[str, object] = {
            "approval_mode": mode,
            "approval_reason": reason,
            "tool_name": tool_name,
        }
        if question_id:
            metadata["question_id"] = question_id
        return ToolResult.error(
            error_code=APPROVAL_REQUIRED_ERROR_CODE,
            reason=reason,
            metadata=metadata,
        )

    def enrich_runtime_metadata(
        self,
        *,
        runtime_metadata: dict[str, object] | None,
        policy: ProfilePolicy,
    ) -> dict[str, object]:
        """Attach policy safety fields to runtime metadata passed into system context."""

        payload: dict[str, object] = dict(runtime_metadata or {})
        preset = self.normalized_policy_preset(policy)
        payload["policy_preset"] = preset
        if preset == "simple":
            payload["safety_confirmation_mode"] = "basic"
        elif preset == "medium":
            payload["safety_confirmation_mode"] = "confirm_file_destructive_ops"
        else:
            payload["safety_confirmation_mode"] = "confirm_all_critical_ops"
        payload["policy_enabled"] = bool(getattr(policy, "policy_enabled", True))
        return payload

    def policy_prompt_block(self, *, policy: ProfilePolicy) -> str:
        """Build runtime safety instructions injected into the main system context."""

        preset = self.normalized_policy_preset(policy)
        if not bool(getattr(policy, "policy_enabled", True)):
            return "Policy is disabled for this profile. Continue with standard safeguards."
        if preset == "simple":
            return (
                "Preset: simple.\n"
                "- Execute normal tool workflow.\n"
                "- Keep following security-secrets and skill instructions."
            )
        if preset == "medium":
            return (
                "Preset: medium.\n"
                "- Before potentially destructive file operations, ask explicit yes/no confirmation.\n"
                "- Destructive examples: file deletion/truncation (`rm`, `del`, `rmdir`, empty overwrite).\n"
                "- If confirmation is missing, return a question instead of executing."
            )
        return (
            "Preset: strict.\n"
            "- Do not execute critical or mutating operations without explicit yes/no confirmation.\n"
            "- Prefer read-only inspection first, then ask for confirmation, then execute.\n"
            "- If confirmation is missing, return a question instead of executing."
        )

    @staticmethod
    def normalized_policy_preset(policy: ProfilePolicy) -> str:
        """Return normalized policy preset string with deterministic fallback."""

        preset = str(getattr(policy, "policy_preset", "medium") or "medium").strip().lower()
        if preset in {"simple", "medium", "strict"}:
            return preset
        return "medium"

    def _approval_requirement(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
        confirmed: bool,
    ) -> tuple[str | None, str]:
        """Resolve runtime approval requirement for one tool call under current preset."""

        if not bool(getattr(policy, "policy_enabled", True)):
            return None, ""
        if confirmed:
            return None, ""

        preset = self.normalized_policy_preset(policy)
        if preset == "simple":
            return None, ""
        if preset == "medium":
            if self._is_medium_destructive_file_operation(tool_name=tool_name, params=params):
                return (
                    "medium",
                    "Medium safety preset requires explicit yes/no confirmation for file deletion/destructive operations.",
                )
            return None, ""
        if preset == "strict" and self._is_strict_critical_operation(
            tool_name=tool_name, params=params
        ):
            return (
                "strict",
                "Strict safety preset requires explicit yes/no confirmation before critical operation execution.",
            )
        return None, ""

    @staticmethod
    def _is_medium_destructive_file_operation(*, tool_name: str, params: dict[str, object]) -> bool:
        """Return whether operation is a potentially destructive file action for medium preset."""

        normalized_tool = tool_name.strip()
        if normalized_tool in {"bash.exec", "session.job.run"}:
            payload_text = "\n".join(
                part for part in SafetyPolicy._shell_payload_values(params) if part
            )
            return _MEDIUM_BASH_DELETE_RE.search(payload_text) is not None
        if normalized_tool == "file.write":
            mode = str(params.get("mode") or "overwrite").strip().lower()
            content = str(params.get("content") or "")
            path = str(params.get("path") or "")
            return (mode == "overwrite" and content == "") or (
                _MEDIUM_DELETE_INTENT_RE.search(path) is not None
            )
        if normalized_tool == "file.edit":
            search = str(params.get("search") or "")
            replace = str(params.get("replace") or "")
            return bool(replace == "" and search) or (
                _MEDIUM_DELETE_INTENT_RE.search(search) is not None
            )
        return False

    @staticmethod
    def _shell_payload_values(value: object, *, field_name: str | None = None) -> list[str]:
        """Return shell payload text values from nested tool params."""

        if isinstance(value, dict):
            result: list[str] = []
            for key, item in value.items():
                result.extend(SafetyPolicy._shell_payload_values(item, field_name=str(key).lower()))
            return result
        if isinstance(value, list):
            list_result: list[str] = []
            for item in value:
                list_result.extend(SafetyPolicy._shell_payload_values(item, field_name=field_name))
            return list_result
        if isinstance(value, str) and field_name in _SHELL_PAYLOAD_PARAM_NAMES:
            return [value]
        return []

    @staticmethod
    def _is_strict_critical_operation(*, tool_name: str, params: dict[str, object]) -> bool:
        """Return whether operation is critical and must be approval-gated in strict preset."""

        normalized_tool = tool_name.strip()
        if normalized_tool in _STRICT_READ_ONLY_TOOL_NAMES:
            return False
        if normalized_tool == "browser.control":
            action = str(params.get("action") or "").strip().lower()
            return action != "content"
        return True
