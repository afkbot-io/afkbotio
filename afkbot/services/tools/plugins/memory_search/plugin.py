"""Tool plugin for scope-aware memory.search."""

from __future__ import annotations

from afkbot.services.memory import (
    MemoryScopeDescriptor,
    MemoryService,
    MemoryServiceError,
    get_memory_service,
)
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemorySearchParams,
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

        try:
            service = get_memory_service(self._settings)
            items = await service.search(
                profile_id=ctx.profile_id,
                query=prepared.query,
                scope=requested_scope,
                visibility=None if not requested_scope.is_profile_scope else "promoted_global",
                memory_kinds=prepared.memory_kinds,
                source_kinds=prepared.source_kinds,
                limit=prepared.limit,
            )
            payload_items = [item.model_dump(mode="json") for item in items]
            if prepared.include_global and not requested_scope.is_profile_scope:
                payload_items = await self._append_global_hits(
                    service=service,
                    ctx=ctx,
                    params=prepared,
                    requested_scope=requested_scope,
                    existing_items=payload_items,
                )
            return ToolResult(ok=True, payload={"items": payload_items})
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)

    async def _append_global_hits(
        self,
        *,
        service: MemoryService,
        ctx: ToolContext,
        params: MemorySearchParams,
        requested_scope: MemoryScopeDescriptor,
        existing_items: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        global_hits = await service.search(
            profile_id=ctx.profile_id,
            query=params.query,
            scope=MemoryScopeDescriptor.profile_scope(session_id=ctx.session_id),
            visibility="promoted_global",
            memory_kinds=params.memory_kinds,
            source_kinds=params.source_kinds,
            limit=params.global_limit or params.limit,
        )
        seen_keys = {
            (str(item.get("scope_key") or ""), str(item.get("memory_key") or ""))
            for item in existing_items
        }
        merged = list(existing_items)
        for item in global_hits:
            if requested_scope.is_profile_scope and item.scope_kind == "profile":
                continue
            dedupe_key = (item.scope_key, item.memory_key)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            merged.append(item.model_dump(mode="json"))
        return merged


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.search tool instance."""

    return MemorySearchTool(settings=settings)
