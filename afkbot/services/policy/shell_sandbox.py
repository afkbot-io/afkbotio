"""Shell sandbox policy helpers shared by setup, profiles, and tool runtime."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

ShellSandboxMode = Literal["disabled", "best_effort", "required"]

SHELL_SANDBOX_MODE_VALUES: tuple[ShellSandboxMode, ...] = (
    "disabled",
    "best_effort",
    "required",
)


def normalize_shell_sandbox_mode(value: object) -> ShellSandboxMode:
    """Validate one shell sandbox mode value."""

    if not isinstance(value, str):
        raise ValueError("shell sandbox mode must be a string")
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in SHELL_SANDBOX_MODE_VALUES:
        allowed = ", ".join(SHELL_SANDBOX_MODE_VALUES)
        raise ValueError(f"shell sandbox mode must be one of: {allowed}")
    return normalized  # type: ignore[return-value]


def default_shell_sandbox_mode(
    *,
    policy_enabled: bool,
    capabilities: Iterable[str],
    workspace_scope_mode: str,
) -> ShellSandboxMode:
    """Return the safe default for shell execution under one profile policy."""

    capability_set = {str(item).strip().lower() for item in capabilities}
    if not policy_enabled or "shell" not in capability_set:
        return "disabled"
    if workspace_scope_mode.strip().lower() == "full_system":
        return "disabled"
    return "required"


def scope_requires_shell_sandbox(scope_roots: tuple[Path, ...]) -> bool:
    """Return whether shell execution needs OS sandboxing for these scope roots."""

    if not scope_roots:
        return False
    root_path = Path("/").resolve(strict=False)
    return not any(root.resolve(strict=False) == root_path for root in scope_roots)
