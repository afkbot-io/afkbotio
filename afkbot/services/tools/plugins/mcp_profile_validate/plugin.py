"""Tool plugin for mcp.profile.validate."""

from __future__ import annotations

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.mcp_profile_shared import mcp_tool_error
from afkbot.settings import Settings


class MCPProfileValidateTool(ToolBase):
    """Validate profile-local MCP config files."""

    name = "mcp.profile.validate"
    description = "Validate profile-local MCP config files."
    required_skill = "mcp-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = self._prepare_params(
            ctx=ctx,
            params=params,
            expected=ToolParameters,
        )
        if isinstance(payload, ToolResult):
            return payload
        try:
            report = await get_mcp_profile_service(self._settings).validate(profile_id=ctx.profile_id)
            return ToolResult(ok=True, payload={"report": report.model_dump(mode="json")})
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            return mcp_tool_error(exc)


def create_tool(settings: Settings) -> ToolBase:
    """Create mcp.profile.validate tool instance."""

    return MCPProfileValidateTool(settings=settings)
