"""Tool plugin for memory.promote."""

from __future__ import annotations

from afkbot.services.memory import MemoryServiceError, get_memory_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemoryPromoteParams,
    resolve_memory_scope_for_operation,
)
from afkbot.settings import Settings


class MemoryPromoteTool(ToolBase):
    """Promote one local semantic memory item into profile-global memory."""

    name = "memory.promote"
    description = "Promote one local semantic memory item into profile-global memory."
    parameters_model = MemoryPromoteParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MemoryPromoteParams)
        if isinstance(prepared, ToolResult):
            return prepared
        from_scope = await resolve_memory_scope_for_operation(
            settings=self._settings,
            ctx=ctx,
            params=prepared,
            operation="promote",
        )
        if isinstance(from_scope, ToolResult):
            return from_scope

        try:
            service = get_memory_service(self._settings)
            item = await service.promote(
                profile_id=ctx.profile_id,
                memory_key=prepared.memory_key,
                from_scope=from_scope,
                target_memory_key=prepared.target_memory_key,
            )
            return ToolResult(ok=True, payload={"item": item.model_dump(mode="json")})
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.promote tool instance."""

    return MemoryPromoteTool(settings=settings)
