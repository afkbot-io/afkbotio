"""Execution posture helpers for current-host autonomy and blocker messaging."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.tools.base import ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class ExecutionBlocker:
    """Deterministic blocker classification for current-host execution flows."""

    error_code: str
    message: str


def first_execution_blocker(
    *,
    tool_calls: list[ToolCall],
    tool_results: list[ToolResult],
) -> ExecutionBlocker | None:
    """Return the first deterministic execution blocker from one tool batch."""

    if len(tool_calls) != len(tool_results):
        return None
    for tool_call, tool_result in zip(tool_calls, tool_results, strict=True):
        error_code = str(tool_result.error_code or "").strip()
        reason = str(tool_result.reason or "").strip()
        if error_code == "tool_not_allowed_in_turn":
            continue
        if error_code == "profile_policy_violation":
            details = f" Reason: {reason}" if reason else ""
            return ExecutionBlocker(
                error_code=error_code,
                message=(
                    "The requested operation is blocked by the current profile policy."
                    f"{details}"
                ),
            )
        if error_code == "tool_blocked_in_user_channel":
            return ExecutionBlocker(
                error_code=error_code,
                message=(
                    "The requested operation is blocked in this user-facing channel. "
                    "Use CLI or another trusted operator surface for credential-management actions."
                ),
            )
        if error_code == "tool_blocked_by_channel_profile":
            channel_profile = str(tool_result.metadata.get("channel_tool_profile") or "").strip()
            profile_suffix = f" `{channel_profile}`" if channel_profile else ""
            return ExecutionBlocker(
                error_code=error_code,
                message=(
                    "The requested operation is blocked by the active channel tool profile"
                    f"{profile_suffix}. Use a more trusted surface or widen the channel "
                    "configuration before retrying."
                ),
            )
    return None
