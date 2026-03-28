"""Tool plugin for app.list."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.apps.action_schema import build_action_schema_manifest
from afkbot.services.apps.registry import get_app_registry
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class AppListParams(ToolParameters):
    """Parameters for app.list tool."""

    include_source: bool = Field(default=True)


class AppListTool(ToolBase):
    """List available apps from builtin and profile-local registries."""

    name = "app.list"
    description = "List available apps with actions, routed skills, and credential requirements."
    parameters_model = AppListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = AppListParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        registry = get_app_registry(
            settings=self._settings,
            profile_id=ctx.profile_id,
        )
        apps: list[dict[str, object]] = []
        for definition in registry.list():
            item: dict[str, object] = {
                "name": definition.name,
                "allowed_actions": sorted(definition.allowed_actions),
                "allowed_skills": sorted(definition.allowed_skills),
            }
            if definition.credential_manifest is not None:
                item["credential_manifest"] = definition.credential_manifest.serialize()
            if definition.action_params_models:
                item["action_schemas"] = {
                    action: build_action_schema_manifest(action=action, model=model).serialize()
                    for action, model in sorted(definition.action_params_models.items())
                }
            if payload.include_source:
                item["source"] = definition.source
                if definition.source_path is not None:
                    item["source_path"] = definition.source_path
            apps.append(item)

        return ToolResult(
            ok=True,
            payload={
                "apps": apps,
                "count": len(apps),
            },
        )


def create_tool(settings: Settings) -> ToolBase:
    """Create app.list tool instance."""

    return AppListTool(settings=settings)
