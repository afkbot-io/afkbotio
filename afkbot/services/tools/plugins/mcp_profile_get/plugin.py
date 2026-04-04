"""Tool plugin for mcp.profile.get."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.mcp_profile_shared import mcp_tool_error
from afkbot.settings import Settings


class MCPProfileGetParams(ToolParameters):
    """Parameters for mcp.profile.get."""

    server: str = Field(min_length=1)


class MCPProfileGetTool(ToolBase):
    """Return one configured MCP server for one profile."""

    name = "mcp.profile.get"
    description = "Return one configured MCP server for one profile."
    parameters_model = MCPProfileGetParams
    required_skill = "mcp-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = self._prepare_params(
            ctx=ctx,
            params=params,
            expected=MCPProfileGetParams,
        )
        if isinstance(payload, ToolResult):
            return payload
        try:
            item = await get_mcp_profile_service(self._settings).get(
                profile_id=ctx.profile_id,
                server=payload.server,
            )
            return ToolResult(ok=True, payload={"server": item.model_dump(mode="json")})
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            return mcp_tool_error(exc)


def create_tool(settings: Settings) -> ToolBase:
    """Create mcp.profile.get tool instance."""

    return MCPProfileGetTool(settings=settings)
