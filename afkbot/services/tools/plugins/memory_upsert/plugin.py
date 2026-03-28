"""Tool plugin for scope-aware memory.upsert."""

from __future__ import annotations

from afkbot.services.memory import MemoryServiceError, get_memory_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemoryWriteParams,
    resolve_memory_scope_for_operation,
)
from afkbot.settings import Settings


class MemoryUpsertTool(ToolBase):
    """Create or update one scoped semantic memory item."""

    name = "memory.upsert"
    description = "Create or update one scoped semantic memory item."
    parameters_model = MemoryWriteParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MemoryWriteParams)
        if isinstance(prepared, ToolResult):
            return prepared
        requested_scope = await resolve_memory_scope_for_operation(
            settings=self._settings,
            ctx=ctx,
            params=prepared,
            operation="upsert",
        )
        if isinstance(requested_scope, ToolResult):
            return requested_scope

        try:
            service = get_memory_service(self._settings)
            item = await service.upsert(
                profile_id=ctx.profile_id,
                scope=requested_scope,
                memory_key=prepared.memory_key,
                content=prepared.content,
                summary=prepared.summary,
                details_md=prepared.details_md,
                source=prepared.source,
                source_kind=prepared.source_kind,
                memory_kind=prepared.memory_kind,
                visibility=prepared.visibility,
            )
            return ToolResult(ok=True, payload={"item": item.model_dump(mode="json")})
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.upsert tool instance."""

    return MemoryUpsertTool(settings=settings)
