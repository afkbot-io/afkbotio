"""Shared contracts and helpers for scoped memory tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from afkbot.services.memory.contracts import (
    MemoryKind,
    MemoryScopeDescriptor,
    MemoryScopeMode,
    MemorySourceKind,
    MemoryVisibility,
)
from afkbot.services.memory.runtime_scope import (
    MemoryOperation,
    MemoryScopeResolutionError,
    resolve_tool_requested_scope,
    user_facing_scope_access_error,
)
from afkbot.services.channel_routing.policy import is_user_facing_transport
from afkbot.services.tools.base import ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class MemoryScopedParams(ToolParameters):
    """Base params for memory tools that may target one explicit scope."""

    scope: MemoryScopeMode = "auto"
    binding_id: str | None = None
    transport: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None

    @field_validator(
        "binding_id",
        "transport",
        "account_id",
        "peer_id",
        "thread_id",
        "user_id",
        "session_id",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

class MemorySearchParams(MemoryScopedParams):
    """Parameters for scoped semantic memory search."""

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    include_global: bool = False
    global_limit: int | None = Field(default=None, ge=1, le=20)
    memory_kinds: tuple[MemoryKind, ...] | None = None
    source_kinds: tuple[MemorySourceKind, ...] | None = None


class MemoryWriteParams(MemoryScopedParams):
    """Base params for scoped memory write operations."""

    memory_key: str = Field(min_length=1, max_length=128)
    source: str | None = Field(default=None, max_length=128)
    source_kind: MemorySourceKind = "manual"
    memory_kind: MemoryKind = "note"
    visibility: MemoryVisibility | None = None
    summary: str | None = None
    details_md: str | None = None
    content: str | None = None


class MemoryDeleteParams(MemoryScopedParams):
    """Parameters for scoped memory deletion."""

    memory_key: str = Field(min_length=1, max_length=128)


class MemoryListParams(MemoryScopedParams):
    """Parameters for scoped memory listing."""

    limit: int = Field(default=50, ge=1, le=200)
    visibility: MemoryVisibility | None = None
    memory_kinds: tuple[MemoryKind, ...] | None = None
    source_kinds: tuple[MemorySourceKind, ...] | None = None


class MemoryDigestParams(MemoryScopedParams):
    """Parameters for deterministic scoped memory digest rendering."""

    limit: int = Field(default=20, ge=1, le=200)
    include_global: bool = False
    global_limit: int | None = Field(default=None, ge=1, le=50)
    visibility: MemoryVisibility | None = None
    memory_kinds: tuple[MemoryKind, ...] | None = None
    source_kinds: tuple[MemorySourceKind, ...] | None = None


class MemoryPromoteParams(MemoryScopedParams):
    """Parameters for promoting one local memory item to profile-global memory."""

    memory_key: str = Field(min_length=1, max_length=128)
    target_memory_key: str | None = Field(default=None, max_length=128)


def ensure_memory_scope_allowed(
    *,
    ctx: ToolContext,
    requested_scope: MemoryScopeDescriptor,
    operation: Literal["search", "list", "upsert", "delete", "promote"],
) -> ToolResult | None:
    """Return deterministic scope error for forbidden user-facing memory access."""

    error = user_facing_scope_access_error(
        ctx=ctx,
        requested_scope=requested_scope,
        operation=operation,
    )
    if error is None:
        return None
    error_code, reason = error
    return ToolResult.error(error_code=error_code, reason=reason)


async def resolve_memory_scope_for_operation(
    *,
    settings: Settings,
    ctx: ToolContext,
    params: MemoryScopedParams,
    operation: MemoryOperation,
) -> MemoryScopeDescriptor | ToolResult:
    """Resolve and validate one requested memory scope for the current tool operation."""

    try:
        requested_scope = await resolve_tool_requested_scope(
            settings=settings,
            ctx=ctx,
            scope_mode=params.scope,
            transport=params.transport,
            account_id=params.account_id,
            peer_id=params.peer_id,
            thread_id=params.thread_id,
            user_id=params.user_id,
            session_id=params.session_id,
            binding_id=params.binding_id,
        )
    except MemoryScopeResolutionError as exc:
        if _should_sanitize_scope_resolution_error(ctx=ctx, error_code=exc.error_code):
            return ToolResult.error(
                error_code="memory_cross_scope_forbidden",
                reason="User-facing channels may not access memory from another chat scope.",
            )
        return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
    scope_error = ensure_memory_scope_allowed(
        ctx=ctx,
        requested_scope=requested_scope,
        operation=operation,
    )
    if scope_error is not None:
        return scope_error
    return requested_scope


def _should_sanitize_scope_resolution_error(*, ctx: ToolContext, error_code: str) -> bool:
    metadata = ctx.runtime_metadata or {}
    transport = metadata.get("transport")
    normalized_transport = transport.strip() if isinstance(transport, str) else None
    if not is_user_facing_transport(normalized_transport):
        return False
    return error_code.startswith("memory_scope_binding_")
