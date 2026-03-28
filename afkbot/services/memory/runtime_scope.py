"""Runtime helpers for resolving, expanding, and restricting scoped memory access."""

from __future__ import annotations

from typing import Literal

from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.policy import is_user_facing_transport
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    get_channel_binding_service,
)
from afkbot.services.memory.contracts import MemoryScopeDescriptor, MemoryScopeMode
from afkbot.services.tools.base import ToolContext
from afkbot.settings import Settings

MemoryOperation = Literal["search", "list", "upsert", "delete", "promote"]


class MemoryScopeResolutionError(ValueError):
    """Structured resolution error for explicit memory scope selectors."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def resolve_runtime_scope(
    *,
    session_id: str,
    runtime_metadata: dict[str, object] | None,
    scope_mode: MemoryScopeMode,
) -> MemoryScopeDescriptor:
    """Resolve one memory scope descriptor from turn runtime metadata."""

    metadata = runtime_metadata or {}
    binding_id = _extract_binding_id(metadata)
    if scope_mode == "profile":
        return MemoryScopeDescriptor.profile_scope(session_id=session_id, binding_id=binding_id)
    transport = _extract_text(metadata, "transport")
    account_id = _extract_text(metadata, "account_id")
    peer_id = _extract_text(metadata, "peer_id")
    thread_id = _extract_text(metadata, "thread_id")
    user_id = _extract_text(metadata, "user_id")
    if scope_mode == "thread" and all((transport, account_id, peer_id, thread_id)):
        return MemoryScopeDescriptor(
            scope_kind="thread",
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            session_id=session_id,
            binding_id=binding_id,
        )
    if scope_mode == "user_in_chat" and all((transport, account_id, peer_id, user_id)):
        return MemoryScopeDescriptor(
            scope_kind="user_in_chat",
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
            session_id=session_id,
            binding_id=binding_id,
        )
    if scope_mode == "auto" and all((transport, account_id, peer_id, user_id)):
        return MemoryScopeDescriptor(
            scope_kind="user_in_chat",
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
            session_id=session_id,
            binding_id=binding_id,
        )
    if scope_mode == "auto" and all((transport, account_id, peer_id, thread_id)):
        return MemoryScopeDescriptor(
            scope_kind="thread",
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            session_id=session_id,
            binding_id=binding_id,
        )
    if all((transport, account_id, peer_id)):
        return MemoryScopeDescriptor(
            scope_kind="chat",
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            session_id=session_id,
            binding_id=binding_id,
        )
    return MemoryScopeDescriptor.profile_scope(session_id=session_id, binding_id=binding_id)


async def resolve_requested_scope(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str,
    runtime_metadata: dict[str, object] | None,
    scope_mode: MemoryScopeMode,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    requested_session_id: str | None,
    binding_id: str | None,
) -> MemoryScopeDescriptor:
    """Resolve one requested memory scope from selectors, runtime metadata, and optional binding id."""

    runtime_scope = resolve_runtime_scope(
        session_id=session_id,
        runtime_metadata=runtime_metadata,
        scope_mode="auto",
    )
    binding_defaults = await _resolve_binding_defaults(
        settings=settings,
        profile_id=profile_id,
        binding_id=binding_id,
    )
    if binding_defaults is not None:
        conflict = _binding_selector_conflict(
            binding=binding_defaults,
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        )
        if conflict is not None:
            raise MemoryScopeResolutionError(
                error_code="memory_scope_binding_conflict",
                reason=(
                    f"Explicit selector `{conflict[0]}` conflicts with binding "
                    f"'{binding_defaults.binding_id}'. Use either the binding id or matching selectors."
                ),
            )
    if scope_mode == "auto" and not any(
        (
            transport,
            account_id,
            peer_id,
            thread_id,
            user_id,
            binding_defaults.binding_id if binding_defaults is not None else None,
        )
    ):
        return runtime_scope
    resolved_session_id = (requested_session_id or "").strip() or session_id
    resolved_binding_id = (
        _normalize_optional_text(binding_id)
        or (None if binding_defaults is None else binding_defaults.binding_id)
        or runtime_scope.binding_id
    )
    if scope_mode == "profile":
        return MemoryScopeDescriptor.profile_scope(
            session_id=resolved_session_id,
            binding_id=resolved_binding_id,
        )
    resolved_transport = (
        _normalize_optional_text(transport)
        or (None if binding_defaults is None else binding_defaults.transport)
        or runtime_scope.transport
    )
    resolved_account_id = (
        _normalize_optional_text(account_id)
        or (None if binding_defaults is None else binding_defaults.account_id)
        or runtime_scope.account_id
    )
    resolved_peer_id = (
        _normalize_optional_text(peer_id)
        or (None if binding_defaults is None else binding_defaults.peer_id)
        or runtime_scope.peer_id
    )
    resolved_thread_id = (
        _normalize_optional_text(thread_id)
        or (None if binding_defaults is None else binding_defaults.thread_id)
        or runtime_scope.thread_id
    )
    resolved_user_id = (
        _normalize_optional_text(user_id)
        or (None if binding_defaults is None else binding_defaults.user_id)
        or runtime_scope.user_id
    )
    effective_scope_kind = scope_mode if scope_mode != "auto" else _infer_scope_kind(
        thread_id=resolved_thread_id,
        user_id=resolved_user_id,
        transport=resolved_transport,
        account_id=resolved_account_id,
        peer_id=resolved_peer_id,
    )
    if binding_defaults is not None and effective_scope_kind == "profile":
        raise MemoryScopeResolutionError(
            error_code="memory_scope_binding_too_broad",
            reason=(
                f"Binding '{binding_defaults.binding_id}' does not identify one concrete chat scope. "
                "Provide explicit peer/thread/user selectors or use a narrower binding."
            ),
        )
    return MemoryScopeDescriptor(
        scope_kind=effective_scope_kind,
        transport=resolved_transport,
        account_id=resolved_account_id,
        peer_id=resolved_peer_id,
        thread_id=resolved_thread_id,
        user_id=resolved_user_id,
        session_id=resolved_session_id,
        binding_id=resolved_binding_id,
    )


async def resolve_tool_requested_scope(
    *,
    settings: Settings,
    ctx: ToolContext,
    scope_mode: MemoryScopeMode,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    session_id: str | None,
    binding_id: str | None,
) -> MemoryScopeDescriptor:
    """Resolve one requested memory scope for a tool invocation."""

    return await resolve_requested_scope(
        settings=settings,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
        runtime_metadata=ctx.runtime_metadata,
        scope_mode=scope_mode,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
        requested_session_id=session_id,
        binding_id=binding_id,
    )


def user_facing_scope_access_error(
    *,
    ctx: ToolContext,
    requested_scope: MemoryScopeDescriptor,
    operation: MemoryOperation,
) -> tuple[str, str] | None:
    """Return fail-closed cross-scope error for user-facing turns when needed."""

    metadata = ctx.runtime_metadata or {}
    transport = _extract_text(metadata, "transport")
    if not _is_user_facing_runtime(metadata):
        return None
    current_scope = resolve_runtime_scope(
        session_id=ctx.session_id,
        runtime_metadata=metadata,
        scope_mode="auto",
    )
    if operation == "promote":
        return (
            "memory_cross_scope_forbidden",
            "Promoting memory from a user-facing channel is not allowed.",
        )
    if requested_scope.is_profile_scope:
        if operation in {"upsert", "delete"}:
            return (
                "memory_cross_scope_forbidden",
                "User-facing channels may not write or delete profile-global memory.",
            )
        return None
    if transport is None:
        return (
            "memory_cross_scope_forbidden",
            "User-facing channels may not access scoped memory when runtime transport metadata is missing.",
        )
    if _is_same_or_parent_local_scope(
        requested_scope=requested_scope,
        current_scope=current_scope,
    ):
        return None
    return (
        "memory_cross_scope_forbidden",
        "User-facing channels may only access the current chat scope automatically.",
    )


def _infer_scope_kind(
    *,
    thread_id: str | None,
    user_id: str | None,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
) -> Literal["profile", "chat", "thread", "user_in_chat"]:
    if all((transport, account_id, peer_id, user_id)):
        return "user_in_chat"
    if all((transport, account_id, peer_id, thread_id)):
        return "thread"
    if all((transport, account_id, peer_id)):
        return "chat"
    return "profile"


def _extract_text(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _extract_binding_id(metadata: dict[str, object]) -> str | None:
    raw = metadata.get("channel_binding")
    if not isinstance(raw, dict):
        return None
    value = raw.get("binding_id")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


async def _resolve_binding_defaults(
    *,
    settings: Settings,
    profile_id: str,
    binding_id: str | None,
) -> ChannelBindingRule | None:
    normalized_binding_id = _normalize_optional_text(binding_id)
    if normalized_binding_id is None:
        return None
    try:
        binding = await get_channel_binding_service(settings).get(binding_id=normalized_binding_id)
    except ChannelBindingServiceError as exc:
        if exc.error_code == "channel_binding_not_found":
            raise MemoryScopeResolutionError(
                error_code="memory_scope_binding_not_found",
                reason=f"Channel binding not found: {normalized_binding_id}",
            ) from None
        raise MemoryScopeResolutionError(
            error_code="memory_scope_binding_error",
            reason=exc.reason,
        ) from None
    if binding.profile_id != profile_id:
        raise MemoryScopeResolutionError(
            error_code="memory_scope_binding_profile_mismatch",
            reason=(
                f"Channel binding '{binding.binding_id}' belongs to profile "
                f"'{binding.profile_id}', not '{profile_id}'."
            ),
        )
    return binding


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _is_user_facing_runtime(metadata: dict[str, object]) -> bool:
    transport = _extract_text(metadata, "transport")
    if is_user_facing_transport(transport):
        return True
    if _extract_binding_id(metadata) is None:
        return False
    return any(
        _extract_text(metadata, key) is not None
        for key in ("account_id", "peer_id", "thread_id", "user_id")
    )


def _is_same_or_parent_local_scope(
    *,
    requested_scope: MemoryScopeDescriptor,
    current_scope: MemoryScopeDescriptor,
) -> bool:
    if requested_scope.scope_key() == current_scope.scope_key():
        return True
    if requested_scope.scope_kind == "chat":
        return _same_chat_base(requested_scope=requested_scope, current_scope=current_scope)
    if requested_scope.scope_kind == "thread":
        return _same_chat_base(
            requested_scope=requested_scope,
            current_scope=current_scope,
        ) and requested_scope.thread_id == current_scope.thread_id
    return False


def _same_chat_base(
    *,
    requested_scope: MemoryScopeDescriptor,
    current_scope: MemoryScopeDescriptor,
) -> bool:
    return (
        requested_scope.transport == current_scope.transport
        and requested_scope.account_id == current_scope.account_id
        and requested_scope.peer_id == current_scope.peer_id
    )


def _binding_selector_conflict(
    *,
    binding: ChannelBindingRule,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> tuple[str, str, str] | None:
    pairs = (
        ("transport", _normalize_optional_text(transport), _normalize_optional_text(binding.transport)),
        ("account_id", _normalize_optional_text(account_id), _normalize_optional_text(binding.account_id)),
        ("peer_id", _normalize_optional_text(peer_id), _normalize_optional_text(binding.peer_id)),
        ("thread_id", _normalize_optional_text(thread_id), _normalize_optional_text(binding.thread_id)),
        ("user_id", _normalize_optional_text(user_id), _normalize_optional_text(binding.user_id)),
    )
    for field_name, explicit_value, binding_value in pairs:
        if explicit_value is None or binding_value is None:
            continue
        if explicit_value != binding_value:
            return (field_name, explicit_value, binding_value)
    return None
