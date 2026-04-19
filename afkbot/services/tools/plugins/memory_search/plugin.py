"""Tool plugin for scope-aware memory.search."""

from __future__ import annotations

from afkbot.services.memory import MemoryServiceError, get_memory_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemorySearchParams,
    ensure_memory_scope_allowed,
    resolve_memory_scope_for_operation,
)
from afkbot.settings import Settings


class MemorySearchTool(ToolBase):
    """Semantic search over chat-local or profile-global memory."""

    name = "memory.search"
    description = "Search scoped semantic memory by semantic similarity."
    parameters_model = MemorySearchParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MemorySearchParams)
        if isinstance(prepared, ToolResult):
            return prepared
        requested_scope = await resolve_memory_scope_for_operation(
            settings=self._settings,
            ctx=ctx,
            params=prepared,
            operation="search",
        )
        if isinstance(requested_scope, ToolResult):
            return requested_scope
        scope_error = ensure_memory_scope_allowed(
            ctx=ctx,
            requested_scope=requested_scope,
            operation="search",
        )
        if scope_error is not None:
            return ToolResult.error(error_code=scope_error[0], reason=scope_error[1])

        try:
            service = get_memory_service(self._settings)
            items = await service.search(
                profile_id=ctx.profile_id,
                query=prepared.query,
                scope=requested_scope,
                visibility=None if not requested_scope.is_profile_scope else "promoted_global",
                memory_kinds=prepared.memory_kinds,
                source_kinds=prepared.source_kinds,
                include_global=prepared.include_global and not requested_scope.is_profile_scope,
                global_limit=prepared.global_limit,
                limit=prepared.limit,
            )
            payload_items = [item.model_dump(mode="json") for item in items]
            return ToolResult(ok=True, payload={"items": payload_items})
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.search tool instance."""

    return MemorySearchTool(settings=settings)
