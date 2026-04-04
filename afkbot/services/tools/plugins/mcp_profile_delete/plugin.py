"""Tool plugin for mcp.profile.delete."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.mcp_profile_shared import mcp_tool_error
from afkbot.settings import Settings


class MCPProfileDeleteParams(ToolParameters):
    """Parameters for mcp.profile.delete."""

    server: str = Field(min_length=1)


class MCPProfileDeleteTool(ToolBase):
    """Remove one operator-managed MCP server config entry."""

    name = "mcp.profile.delete"
    description = "Remove one operator-managed MCP server config entry."
    parameters_model = MCPProfileDeleteParams
    required_skill = "mcp-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = self._prepare_params(
            ctx=ctx,
            params=params,
            expected=MCPProfileDeleteParams,
        )
        if isinstance(payload, ToolResult):
            return payload
        try:
            result = await get_mcp_profile_service(self._settings).remove(
                profile_id=ctx.profile_id,
                server=payload.server,
            )
            return ToolResult(ok=True, payload={"result": result.model_dump(mode="json")})
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            return mcp_tool_error(exc)


def create_tool(settings: Settings) -> ToolBase:
    """Create mcp.profile.delete tool instance."""

    return MCPProfileDeleteTool(settings=settings)
