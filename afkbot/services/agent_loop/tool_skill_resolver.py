"""Skill resolution helpers for tool calls and app-backed integrations."""

from __future__ import annotations

from afkbot.services.apps.registry import AppRegistry, get_app_registry
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


class ToolSkillResolver:
    """Resolve required skills for tool calls, including dynamic app routing."""

    def __init__(
        self,
        *,
        settings: Settings,
        tool_registry: ToolRegistry | None,
    ) -> None:
        self._settings = settings
        self._tool_registry = tool_registry

    def required_skill_for_tool(
        self,
        *,
        tool_name: str,
        params: dict[str, object] | None = None,
        profile_id: str | None = None,
    ) -> str | None:
        """Return resolved required skill name for one tool call, if available."""

        required_skills = self.required_skills_for_tool_call(
            tool_name=tool_name,
            params=params,
            profile_id=profile_id,
        )
        if not required_skills:
            return None
        if len(required_skills) == 1:
            return next(iter(required_skills))
        return None

    def required_skills_for_tool_call(
        self,
        *,
        tool_name: str,
        params: dict[str, object] | None,
        profile_id: str | None,
    ) -> set[str]:
        """Return allowed skill names for one tool call, including app skills."""

        if self._tool_registry is None:
            return set()
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return set()
        required = str(tool.required_skill or "").strip()
        if required:
            return {required}
        if tool_name != "app.run":
            return set()
        app_name = str((params or {}).get("app_name") or "").strip().lower()
        if not app_name:
            return set()
        app_definition = self.app_registry(profile_id=profile_id).get(app_name)
        if app_definition is None:
            return set()
        return set(app_definition.allowed_skills)

    def app_registry(self, *, profile_id: str | None) -> AppRegistry:
        """Return app registry for one profile, including profile-local apps."""

        if profile_id:
            return get_app_registry(settings=self._settings, profile_id=profile_id)
        return get_app_registry()
