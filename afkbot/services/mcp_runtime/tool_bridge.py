"""Runtime MCP tool bridge exposed through the core ToolRegistry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ConfigDict, Field

from afkbot.services.mcp_runtime.service import MCPRuntimeService
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import (
    ToolParameters,
    ToolParametersValidationError,
    build_tool_parameters,
)
from afkbot.settings import Settings

_BASE_PARAM_NAMES = frozenset({"profile_id", "profile_key", "timeout_sec"})


class MCPToolsListParams(ToolParameters):
    """Parameters for runtime MCP tool discovery."""

    server: str | None = Field(default=None, min_length=1)


class MCPToolsCallParams(ToolParameters):
    """Parameters for runtime MCP tool execution."""

    server: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)


class _MCPListSchema(ToolParameters):
    """LLM-visible schema for `mcp.tools.list`."""

    model_config = ConfigDict(extra="forbid")

    server: str | None = Field(
        default=None,
        description="Optional configured MCP server id. Omit to inspect all runtime-accessible MCP servers.",
    )


class _MCPCallSchema(ToolParameters):
    """LLM-visible schema for `mcp.tools.call`."""

    model_config = ConfigDict(extra="forbid")

    server: str = Field(description="Configured MCP server id returned by mcp.tools.list.")
    tool_name: str = Field(description="Exact remote tool name returned by mcp.tools.list.")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        description="Remote MCP tool arguments object matching the listed input schema.",
    )


class MCPToolsListTool(ToolBase):
    """List tools exposed by profile-configured remote MCP servers."""

    name: ClassVar[str] = "mcp.tools.list"
    description: ClassVar[str] = (
        "List live tools exposed by profile-configured remote MCP servers."
        " Use this before mcp.tools.call when you need exact tool names and schemas."
    )
    parameters_model: ClassVar[type[ToolParameters]] = _MCPListSchema

    def __init__(self, *, service: MCPRuntimeService, profile_id: str) -> None:
        self._service = service
        self._profile_id = profile_id

    def policy_params(
        self,
        raw_params: Mapping[str, object],
        *,
        ctx: ToolContext | None = None,
    ) -> dict[str, object]:
        profile_id = self._profile_id if ctx is None else ctx.profile_id
        server = str(raw_params.get("server") or "").strip() or None
        params: dict[str, object] = {}
        endpoints = [
            config.url
            for config in self._service.resolve_policy_servers(
                profile_id=profile_id,
                server=server,
            )
            if config.url
        ]
        if server is not None:
            params["server"] = server
        if len(endpoints) == 1:
            params["endpoint_url"] = endpoints[0]
        elif endpoints:
            params["endpoint_urls"] = endpoints
        return params

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MCPToolsListParams)
        if isinstance(prepared, ToolResult):
            return prepared
        try:
            tools = await self._service.list_tools(
                profile_id=prepared.effective_profile_id,
                server=prepared.server,
                timeout_sec=prepared.timeout_sec,
            )
        except Exception as exc:
            return ToolResult.error(
                error_code="tool_execution_failed",
                reason=f"MCP runtime discovery failed: {exc}",
            )
        payload = {
            "servers": sorted({item.server_name for item in tools}),
            "tools": [
                {
                    "server": item.server_name,
                    "transport": item.transport,
                    "runtime_name": item.runtime_name,
                    "tool_name": item.remote_tool_name,
                    "description": item.description,
                    "input_schema": item.input_schema,
                }
                for item in tools
            ],
        }
        return ToolResult(ok=True, payload=payload)


class MCPToolsCallTool(ToolBase):
    """Call one remote tool through the runtime MCP bridge."""

    name: ClassVar[str] = "mcp.tools.call"
    description: ClassVar[str] = (
        "Call one live remote MCP tool by server id and exact remote tool name."
        " Use mcp.tools.list first to inspect the available remote tools and their input schemas."
    )
    parameters_model: ClassVar[type[ToolParameters]] = _MCPCallSchema

    def __init__(self, *, service: MCPRuntimeService, profile_id: str) -> None:
        self._service = service
        self._profile_id = profile_id

    def policy_params(
        self,
        raw_params: Mapping[str, object],
        *,
        ctx: ToolContext | None = None,
    ) -> dict[str, object]:
        profile_id = self._profile_id if ctx is None else ctx.profile_id
        server = str(raw_params.get("server") or "").strip() or None
        params: dict[str, object] = {}
        if server is not None:
            params["server"] = server
        tool_name = str(raw_params.get("tool_name") or "").strip()
        if tool_name:
            params["tool_name"] = tool_name
        endpoints = [
            config.url
            for config in self._service.resolve_policy_servers(
                profile_id=profile_id,
                server=server,
            )
            if config.url
        ]
        if len(endpoints) == 1:
            params["endpoint_url"] = endpoints[0]
        elif endpoints:
            params["endpoint_urls"] = endpoints
        return params

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        base_payload = {
            str(key): value
            for key, value in raw_params.items()
            if str(key) in {"server", "tool_name", *tuple(_BASE_PARAM_NAMES)}
        }
        params = build_tool_parameters(
            MCPToolsCallParams,
            base_payload,
            default_timeout_sec=default_timeout_sec,
            max_timeout_sec=max_timeout_sec,
        )
        if "arguments" in raw_params:
            arguments = raw_params.get("arguments")
            if not isinstance(arguments, dict):
                raise ToolParametersValidationError(reason="arguments must be an object")
            if any(str(key) not in _BASE_PARAM_NAMES | {"server", "tool_name", "arguments"} for key in raw_params):
                raise ToolParametersValidationError(
                    reason="Pass remote MCP arguments inside `arguments` or omit `arguments` and use flat fields, not both.",
                )
            params.arguments = {str(key): value for key, value in arguments.items()}
            return params
        params.arguments = {
            str(key): value
            for key, value in raw_params.items()
            if str(key) not in _BASE_PARAM_NAMES | {"server", "tool_name"}
        }
        if not isinstance(params.arguments, dict):
            raise ToolParametersValidationError(reason="arguments must be an object")
        return params

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MCPToolsCallParams)
        if isinstance(prepared, ToolResult):
            return prepared

        try:
            listed_tools = await self._service.list_tools(
                profile_id=prepared.effective_profile_id,
                server=prepared.server,
                timeout_sec=prepared.timeout_sec,
            )
            matching_tool = next(
                item
                for item in listed_tools
                if (
                    item.server_name == prepared.server.strip().lower()
                    and item.remote_tool_name == prepared.tool_name
                )
            )
        except StopIteration:
            return ToolResult.error(
                error_code="tool_params_invalid",
                reason=(
                    f"MCP tool not found on server {prepared.server.strip().lower()}: "
                    f"{prepared.tool_name}"
                ),
            )
        except Exception as exc:
            return ToolResult.error(
                error_code="tool_execution_failed",
                reason=f"MCP runtime discovery failed: {exc}",
            )

        validation_error = _validate_mcp_arguments(
            tool_name=prepared.tool_name,
            arguments=prepared.arguments,
            input_schema=matching_tool.input_schema,
        )
        if validation_error is not None:
            return validation_error

        try:
            payload = await self._service.call_tool(
                profile_id=prepared.effective_profile_id,
                server=prepared.server,
                tool_name=prepared.tool_name,
                arguments=prepared.arguments,
                timeout_sec=prepared.timeout_sec,
            )
        except Exception as exc:
            return ToolResult.error(
                error_code="tool_execution_failed",
                reason=f"MCP runtime call failed: {exc}",
            )
        if bool(payload.get("is_error")):
            reason = str(payload.get("text") or "Remote MCP tool reported an error").strip()
            return ToolResult.error(
                error_code="tool_execution_failed",
                reason=reason,
                metadata={"server": prepared.server, "tool_name": prepared.tool_name},
            )
        return ToolResult(ok=True, payload=payload)


def build_mcp_runtime_tools(*, settings: Settings, profile_id: str) -> tuple[ToolBase, ...]:
    """Build runtime MCP tool bridge when the profile has eligible server configs."""

    service = MCPRuntimeService(settings=settings)
    if not service.has_runtime_servers(profile_id=profile_id):
        return ()
    return (
        MCPToolsListTool(service=service, profile_id=profile_id),
        MCPToolsCallTool(service=service, profile_id=profile_id),
    )


def _validate_mcp_arguments(
    *,
    tool_name: str,
    arguments: dict[str, object],
    input_schema: dict[str, object],
) -> ToolResult | None:
    validator = Draft202012Validator(input_schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda item: list(item.absolute_path))
    if not errors:
        return None
    first_error = errors[0]
    dotted_path = ".".join(str(part) for part in first_error.absolute_path)
    prefix = f" at {dotted_path}" if dotted_path else ""
    return ToolResult.error(
        error_code="tool_params_invalid",
        reason=f"MCP tool arguments invalid for {tool_name}{prefix}: {first_error.message}",
    )
