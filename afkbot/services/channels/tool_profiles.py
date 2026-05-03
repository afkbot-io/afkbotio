"""Channel-level tool profile presets that narrow profile-level permissions."""

from __future__ import annotations

from typing import Literal

from afkbot.services.profile_runtime.contracts import ProfilePolicyView

ChannelToolProfile = Literal[
    "inherit",
    "chat_minimal",
    "messaging_safe",
    "support_readonly",
    "taskflow_operator",
]

CHANNEL_TOOL_PROFILE_VALUES: tuple[ChannelToolProfile, ...] = (
    "inherit",
    "chat_minimal",
    "messaging_safe",
    "support_readonly",
    "taskflow_operator",
)
DEFAULT_USER_FACING_CHANNEL_TOOL_PROFILE: ChannelToolProfile = "messaging_safe"
CHANNEL_TOOL_PROFILE_HELP = (
    "Channel tool narrowing profile: "
    "inherit, chat_minimal, messaging_safe, support_readonly, taskflow_operator. "
    "Current-channel scoped tools such as channel.history.list can remain available "
    "without opening generic app.run, shell, or filesystem access."
)

_ALLOWED_TOOL_NAMES_BY_PROFILE: dict[ChannelToolProfile, tuple[str, ...] | None] = {
    "inherit": None,
    "chat_minimal": (
        "channel.history.list",
        "channel.send",
    ),
    "messaging_safe": (
        "channel.history.list",
        "channel.send",
        "memory.digest",
        "memory.search",
        "memory.upsert",
    ),
    "support_readonly": (
        "channel.history.list",
        "channel.send",
        "memory.digest",
        "memory.search",
        "memory.upsert",
        "file.list",
        "file.read",
        "file.search",
        "diffs.render",
    ),
    "taskflow_operator": (
        "channel.history.list",
        "channel.send",
        "memory.digest",
        "memory.search",
        "memory.upsert",
        "task.block",
        "task.board",
        "task.comment.add",
        "task.comment.list",
        "task.create",
        "task.delegate",
        "task.dependency.add",
        "task.dependency.list",
        "task.dependency.remove",
        "task.event.list",
        "task.flow.create",
        "task.flow.get",
        "task.flow.list",
        "task.get",
        "task.inbox",
        "task.list",
        "task.review.approve",
        "task.review.list",
        "task.review.request_changes",
        "task.run.get",
        "task.run.list",
        "task.stale.list",
        "task.update",
    ),
}


def normalize_channel_tool_profile(value: object) -> ChannelToolProfile:
    """Normalize one persisted channel tool-profile selector."""

    if not isinstance(value, str):
        raise ValueError("tool_profile must be a string")
    normalized = value.strip().lower()
    if normalized not in CHANNEL_TOOL_PROFILE_VALUES:
        allowed = ", ".join(CHANNEL_TOOL_PROFILE_VALUES)
        raise ValueError(f"tool_profile must be one of: {allowed}")
    return normalized  # type: ignore[return-value]


def allowed_tool_names_for_channel_profile(
    tool_profile: ChannelToolProfile,
) -> tuple[str, ...] | None:
    """Return the explicit allowlist for one channel tool profile."""

    return _ALLOWED_TOOL_NAMES_BY_PROFILE[tool_profile]


def default_channel_tool_profile_for_policy(*, policy: ProfilePolicyView) -> ChannelToolProfile:
    """Return the safest useful default channel tool profile for one profile policy."""

    if _policy_supports_memory(policy=policy) and _policy_supports_readonly_files(policy=policy):
        return "support_readonly"
    if _policy_supports_taskflow(policy=policy) and _policy_supports_memory(policy=policy):
        return "taskflow_operator"
    if _policy_supports_memory(policy=policy):
        return "messaging_safe"
    return "chat_minimal"


def _policy_supports_memory(*, policy: ProfilePolicyView) -> bool:
    if not policy.enabled:
        return True
    return "memory" in set(policy.capabilities)


def _policy_supports_readonly_files(*, policy: ProfilePolicyView) -> bool:
    if not policy.enabled:
        return True
    return "files" in set(policy.capabilities) and policy.file_access_mode in {"read_only", "read_write"}


def _policy_supports_taskflow(*, policy: ProfilePolicyView) -> bool:
    if not policy.enabled:
        return True
    return "taskflow" in set(policy.capabilities)
