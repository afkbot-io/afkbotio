"""Child-agent runtime policy for subagent execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class SubagentRuntimePolicy:
    """Describe isolated child-agent runtime rules for subagent execution."""

    actor: Literal["subagent"] = "subagent"
    child_session_prefix: str = "subagent:"
    disabled_tool_plugins: tuple[str, ...] = (
        "session_job_run",
        "subagent_run",
        "subagent_wait",
        "subagent_result",
    )

    def build_child_settings(self, settings: Settings) -> Settings:
        """Return settings copy with recursive subagent tools removed."""

        plugin_names = tuple(
            name for name in settings.enabled_tool_plugins if name not in self.disabled_tool_plugins
        )
        return Settings(**{**settings.model_dump(), "enabled_tool_plugins": plugin_names})

    def build_child_session_id(self, *, task_id: str) -> str:
        """Return deterministic child session id for one subagent task."""

        return f"{self.child_session_prefix}{task_id}"

    @staticmethod
    def build_prompt_overlay(*, subagent_name: str, subagent_markdown: str) -> str:
        """Return trusted prompt overlay injected into child runtime context."""

        normalized_markdown = subagent_markdown.strip() or "# subagent"
        return (
            "Run as a child subagent for the parent agent. "
            "Use the following subagent-specific instructions.\n"
            f"Subagent: {subagent_name}\n\n"
            f"{normalized_markdown}"
        )


DEFAULT_SUBAGENT_RUNTIME_POLICY = SubagentRuntimePolicy()

__all__ = [
    "DEFAULT_SUBAGENT_RUNTIME_POLICY",
    "SubagentRuntimePolicy",
]
