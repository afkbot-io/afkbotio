"""Tool plugin for mcp.profile.upsert."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.mcp_integration.url_resolver import resolve_mcp_url
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.mcp_profile_shared import mcp_tool_error
from afkbot.settings import Settings


class MCPProfileUpsertParams(ToolParameters):
    """Parameters for mcp.profile.upsert."""

    url: str = Field(min_length=1)
    server: str | None = None
    transport: Literal["http", "sse", "websocket"] | None = None
    capabilities: tuple[Literal["tools", "resources", "prompts"], ...] = ("tools",)
    env_refs: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    enabled: bool = True
    validate_after: bool = True


class MCPProfileUpsertTool(ToolBase):
    """Create or update one profile-local MCP server config from a remote endpoint URL."""

    name = "mcp.profile.upsert"
    description = "Create or update one profile-local MCP server config from a remote endpoint URL."
    parameters_model = MCPProfileUpsertParams
    required_skill = "mcp-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = self._prepare_params(
            ctx=ctx,
            params=params,
            expected=MCPProfileUpsertParams,
        )
        if isinstance(payload, ToolResult):
            return payload

        try:
            resolution = resolve_mcp_url(payload.url)
            service = get_mcp_profile_service(self._settings)
            result = await service.add_by_url(
                profile_id=ctx.profile_id,
                url=resolution.url,
                server=(payload.server or resolution.suggested_server),
                transport=(payload.transport or resolution.suggested_transport),
                capabilities=tuple(payload.capabilities),
                env_refs=tuple(payload.env_refs),
                secret_refs=tuple(payload.secret_refs),
                enabled=payload.enabled,
            )
            response_payload: dict[str, object] = {
                "result": result.model_dump(mode="json"),
            }
            if payload.validate_after:
                validation = await service.validate(profile_id=ctx.profile_id)
                response_payload["validation"] = validation.model_dump(mode="json")
            return ToolResult(ok=True, payload=response_payload)
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            return mcp_tool_error(exc)


def create_tool(settings: Settings) -> ToolBase:
    """Create mcp.profile.upsert tool instance."""

    return MCPProfileUpsertTool(settings=settings)
