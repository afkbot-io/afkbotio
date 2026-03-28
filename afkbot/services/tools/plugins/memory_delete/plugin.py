"""Tool plugin for scope-aware memory.delete."""

from __future__ import annotations

from afkbot.services.memory import MemoryServiceError, get_memory_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemoryDeleteParams,
    resolve_memory_scope_for_operation,
)
from afkbot.settings import Settings


class MemoryDeleteTool(ToolBase):
    """Delete one scoped semantic memory item by logical key."""

    name = "memory.delete"
    description = "Delete one scoped semantic memory item by logical key."
    parameters_model = MemoryDeleteParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MemoryDeleteParams)
        if isinstance(prepared, ToolResult):
            return prepared
        requested_scope = await resolve_memory_scope_for_operation(
            settings=self._settings,
            ctx=ctx,
            params=prepared,
            operation="delete",
        )
        if isinstance(requested_scope, ToolResult):
            return requested_scope

        try:
            service = get_memory_service(self._settings)
            await service.delete(
                profile_id=ctx.profile_id,
                scope=requested_scope,
                memory_key=prepared.memory_key,
            )
            return ToolResult(ok=True, payload={"deleted": True, "memory_key": prepared.memory_key})
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.delete tool instance."""

    return MemoryDeleteTool(settings=settings)
