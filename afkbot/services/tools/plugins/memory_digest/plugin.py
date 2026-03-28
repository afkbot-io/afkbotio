"""Tool plugin for deterministic scope-aware memory.digest."""

from __future__ import annotations

from afkbot.services.memory import (
    MemoryKind,
    MemoryItemMetadata,
    MemoryScopeDescriptor,
    MemorySourceKind,
    MemoryServiceError,
    get_memory_service,
)
from afkbot.services.memory.digest import render_memory_digest
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.memory_shared import (
    MemoryDigestParams,
    resolve_memory_scope_for_operation,
)
from afkbot.settings import Settings


class MemoryDigestTool(ToolBase):
    """Render one deterministic scoped memory digest."""

    name = "memory.digest"
    description = "Render a compact deterministic digest of scoped semantic memory."
    parameters_model = MemoryDigestParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=MemoryDigestParams)
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
            local_items = await service.list(
                profile_id=ctx.profile_id,
                scope=requested_scope,
                visibility=(
                    prepared.visibility if not requested_scope.is_profile_scope else "promoted_global"
                ),
                limit=prepared.limit,
            )
            local_items = _filter_items(
                items=local_items,
                memory_kinds=prepared.memory_kinds,
                source_kinds=prepared.source_kinds,
            )
            global_items: list[MemoryItemMetadata] = []
            if prepared.include_global and not requested_scope.is_profile_scope:
                global_items = await service.list(
                    profile_id=ctx.profile_id,
                    scope=MemoryScopeDescriptor.profile_scope(),
                    visibility="promoted_global",
                    limit=prepared.global_limit or prepared.limit,
                )
                global_items = _filter_items(
                    items=global_items,
                    memory_kinds=prepared.memory_kinds,
                    source_kinds=prepared.source_kinds,
                )
            digest = render_memory_digest(
                scope=requested_scope,
                local_items=local_items,
                global_items=global_items,
            )
            return ToolResult(
                ok=True,
                payload={
                    "scope": requested_scope.model_dump(mode="json"),
                    "item_count": digest.item_count,
                    "local_count": digest.local_count,
                    "global_count": digest.global_count,
                    "kind_counts": digest.kind_counts,
                    "digest_md": digest.digest_md,
                    "items": [item.model_dump(mode="json") for item in [*local_items, *global_items]],
                },
            )
        except MemoryServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.digest tool instance."""

    return MemoryDigestTool(settings=settings)


def _filter_items(
    *,
    items: list[MemoryItemMetadata],
    memory_kinds: tuple[MemoryKind, ...] | None,
    source_kinds: tuple[MemorySourceKind, ...] | None,
) -> list[MemoryItemMetadata]:
    filtered = list(items)
    if memory_kinds:
        allowed_memory_kinds = set(memory_kinds)
        filtered = [item for item in filtered if item.memory_kind in allowed_memory_kinds]
    if source_kinds:
        allowed_source_kinds = set(source_kinds)
        filtered = [item for item in filtered if item.source_kind in allowed_source_kinds]
    return filtered
