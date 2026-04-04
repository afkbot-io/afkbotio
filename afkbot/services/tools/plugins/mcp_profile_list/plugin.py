"""Tool plugin for mcp.profile.list."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.mcp_profile_shared import mcp_tool_error
from afkbot.settings import Settings


class MCPProfileListParams(ToolParameters):
    """Parameters for mcp.profile.list."""

    show_disabled: bool = Field(default=False)


class MCPProfileListTool(ToolBase):
    """List effective MCP servers configured for one profile."""

    name = "mcp.profile.list"
    description = "List effective MCP servers configured for one profile."
    parameters_model = MCPProfileListParams
    required_skill = "mcp-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = self._prepare_params(
            ctx=ctx,
            params=params,
            expected=MCPProfileListParams,
        )
        if isinstance(payload, ToolResult):
            return payload
        try:
            items = await get_mcp_profile_service(self._settings).list(
                profile_id=ctx.profile_id,
                show_disabled=payload.show_disabled,
            )
            return ToolResult(
                ok=True,
                payload={"servers": [item.model_dump(mode="json") for item in items]},
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            return mcp_tool_error(exc)


def create_tool(settings: Settings) -> ToolBase:
    """Create mcp.profile.list tool instance."""

    return MCPProfileListTool(settings=settings)
