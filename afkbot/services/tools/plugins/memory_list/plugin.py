"""Tool plugin for scope-aware memory.list."""

from __future__ import annotations

from afkbot.services.memory import MemoryServiceError, get_memory_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemoryListParams,
    resolve_memory_scope_for_operation,
)
from afkbot.settings import Settings


class MemoryListTool(ToolBase):
    """List scoped semantic memory items for the current profile."""

    name = "memory.list"
    description = "List scoped semantic memory items."
    parameters_model = MemoryListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MemoryListParams)
        if isinstance(prepared, ToolResult):
            return prepared
        requested_scope = await resolve_memory_scope_for_operation(
            settings=self._settings,
            ctx=ctx,
            params=prepared,
            operation="list",
        )
        if isinstance(requested_scope, ToolResult):
            return requested_scope

        try:
            service = get_memory_service(self._settings)
            items = await service.list(
                profile_id=ctx.profile_id,
                scope=requested_scope,
                visibility=(
                    prepared.visibility
                    if not requested_scope.is_profile_scope
                    else "promoted_global"
                ),
                limit=prepared.limit,
            )
            payload_items = [item.model_dump(mode="json") for item in items]
            if prepared.memory_kinds:
                allowed_memory_kinds = set(prepared.memory_kinds)
                payload_items = [
                    item for item in payload_items if str(item.get("memory_kind") or "") in allowed_memory_kinds
                ]
            if prepared.source_kinds:
                allowed_source_kinds = set(prepared.source_kinds)
                payload_items = [
                    item for item in payload_items if str(item.get("source_kind") or "") in allowed_source_kinds
                ]
            return ToolResult(ok=True, payload={"items": payload_items})
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.list tool instance."""

    return MemoryListTool(settings=settings)
