"""Automatic scope-aware memory search/save hooks for AgentLoop turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.memory_extraction import (
    ExtractedMemoryCandidate,
    extract_memory_candidates,
)
from afkbot.services.memory.consolidation import (
    MemoryConsolidationPlan,
    MemoryConsolidationService,
)
from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
from afkbot.services.memory.contracts import MemoryScopeMode
from afkbot.services.memory.runtime_scope import resolve_runtime_scope
from afkbot.services.policy import PolicyEngine, PolicyViolationError
from afkbot.services.tools.base import ToolCall, ToolContext
from afkbot.services.tools.registry import ToolRegistry

AsyncLogEvent = Callable[..., Awaitable[None]]


class MemoryRuntime:
    """Run optional automatic memory search/save hooks around one chat turn."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry | None,
        policy_engine: PolicyEngine,
        tool_execution: ToolExecutionRuntime,
        log_event: AsyncLogEvent,
        auto_search_enabled: bool,
        auto_search_scope_mode: MemoryScopeMode,
        auto_search_limit: int,
        auto_search_include_global: bool,
        auto_search_chat_limit: int,
        auto_search_global_limit: int,
        global_fallback_enabled: bool,
        auto_context_item_chars: int,
        auto_save_enabled: bool,
        auto_save_scope_mode: MemoryScopeMode,
        auto_promote_enabled: bool,
        auto_save_kinds: tuple[str, ...],
        auto_save_max_chars: int,
        consolidation_service: MemoryConsolidationService | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._policy_engine = policy_engine
        self._tool_execution = tool_execution
        self._log_event = log_event
        self._auto_search_enabled = auto_search_enabled
        self._auto_search_scope_mode = auto_search_scope_mode
        self._auto_search_limit = max(1, auto_search_limit)
        self._auto_search_include_global = auto_search_include_global
        self._auto_search_chat_limit = max(1, auto_search_chat_limit)
        self._auto_search_global_limit = max(1, auto_search_global_limit)
        self._global_fallback_enabled = global_fallback_enabled
        self._auto_context_item_chars = max(32, auto_context_item_chars)
        self._auto_save_enabled = auto_save_enabled
        self._auto_save_scope_mode = auto_save_scope_mode
        self._auto_promote_enabled = auto_promote_enabled
        self._auto_save_kinds = tuple(item.strip().lower() for item in auto_save_kinds if item.strip())
        self._auto_save_max_chars = max(64, auto_save_max_chars)
        self._consolidation_service = consolidation_service

    async def auto_search_metadata(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        user_message: str,
        policy: ProfilePolicy,
        runtime_metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Return runtime metadata block from automatic memory search, if enabled."""

        if not self._auto_search_enabled:
            return None
        query = user_message.strip()
        if not query:
            return None
        if self._tool_registry is None or self._tool_registry.get("memory.search") is None:
            return None

        target_scope = resolve_runtime_scope(
            session_id=session_id,
            runtime_metadata=runtime_metadata,
            scope_mode=self._auto_search_scope_mode,
        )
        search_params: dict[str, object] = {
            "profile_id": profile_id,
            "profile_key": profile_id,
            "query": query,
            "scope": self._auto_search_scope_mode,
            "limit": min(self._auto_search_limit, self._auto_search_chat_limit),
            "include_global": self._auto_search_include_global and self._global_fallback_enabled,
            "global_limit": self._auto_search_global_limit,
        }
        try:
            self._policy_engine.ensure_tool_call_allowed(
                policy=policy,
                tool_name="memory.search",
                params=search_params,
            )
        except PolicyViolationError:
            return None

        result = await self._tool_execution.execute_tool_call(
            tool_call=ToolCall(name="memory.search", params=search_params),
            ctx=ToolContext(
                profile_id=profile_id,
                session_id=session_id,
                run_id=run_id,
                runtime_metadata=runtime_metadata,
            ),
        )
        if not result.ok:
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="memory.auto_search",
                payload={
                    "ok": False,
                    "error_code": result.error_code,
                    "reason": result.reason,
                },
            )
            return None

        raw_items = result.payload.get("items")
        if not isinstance(raw_items, list):
            return None
        compact_items: list[dict[str, object]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or item.get("content") or "").strip()
            if not summary:
                continue
            compact_items.append(
                {
                    "memory_key": str(item.get("memory_key") or ""),
                    "summary": summary[: self._auto_context_item_chars],
                    "score": item.get("score"),
                    "memory_kind": item.get("memory_kind"),
                    "scope_kind": item.get("scope_kind"),
                    "visibility": item.get("visibility"),
                    "source_kind": item.get("source_kind"),
                }
            )
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="memory.auto_search",
            payload={
                "ok": True,
                "hits": len(compact_items),
                "scope_kind": target_scope.scope_kind,
                "include_global": self._auto_search_include_global and self._global_fallback_enabled,
            },
        )
        if not compact_items:
            return None
        return {"auto_memory": compact_items}

    async def auto_save_turn(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
        action: str,
        policy: ProfilePolicy,
        runtime_metadata: dict[str, object] | None = None,
    ) -> None:
        """Automatically persist extracted scoped memory records for finalized turns."""

        if not self._auto_save_enabled:
            return
        if action != "finalize":
            return
        if self._tool_registry is None or self._tool_registry.get("memory.upsert") is None:
            return
        candidates = extract_memory_candidates(
            user_message=user_message,
            assistant_message=assistant_message,
            max_chars=self._auto_save_max_chars,
            allowed_kinds=self._auto_save_kinds,
        )
        if not candidates:
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="memory.auto_save",
                payload={"ok": True, "saved": 0},
            )
            return

        saved_keys: list[str] = []
        promoted_keys: list[str] = []
        for candidate in candidates:
            plan = self._build_consolidation_plan(candidate)
            upsert_params: dict[str, object] = {
                "profile_id": profile_id,
                "profile_key": profile_id,
                "scope": self._auto_save_scope_mode,
                "memory_key": plan.memory_key,
                "summary": plan.summary,
                "details_md": plan.details_md,
                "source": "agent_loop.auto",
                "source_kind": "auto",
                "memory_kind": plan.memory_kind,
            }
            try:
                self._policy_engine.ensure_tool_call_allowed(
                    policy=policy,
                    tool_name="memory.upsert",
                    params=upsert_params,
                )
            except PolicyViolationError:
                continue
            result = await self._tool_execution.execute_tool_call(
                tool_call=ToolCall(name="memory.upsert", params=upsert_params),
                ctx=ToolContext(
                    profile_id=profile_id,
                    session_id=session_id,
                    run_id=run_id,
                    runtime_metadata=runtime_metadata,
                ),
            )
            if result.ok:
                saved_keys.append(plan.memory_key)
                if self._consolidation_service is not None and plan.mirror_to_core:
                    try:
                        await self._consolidation_service.mirror_plan_to_core(
                            profile_id=profile_id,
                            plan=plan,
                            source="agent_loop.auto",
                            source_kind="auto",
                        )
                    except Exception as exc:  # noqa: BLE001
                        await self._log_event(
                            run_id=run_id,
                            session_id=session_id,
                            event_type="memory.auto_save_profile_mirror",
                            payload={
                                "ok": False,
                                "memory_key": plan.core_memory_key or plan.memory_key,
                                "reason": str(exc),
                            },
                        )
            if not result.ok or not self._auto_promote_enabled or not plan.promote_global:
                continue
            if self._tool_registry is None or self._tool_registry.get("memory.promote") is None:
                continue
            promote_params: dict[str, object] = {
                "profile_id": profile_id,
                "profile_key": profile_id,
                "scope": self._auto_save_scope_mode,
                "memory_key": plan.memory_key,
            }
            try:
                self._policy_engine.ensure_tool_call_allowed(
                    policy=policy,
                    tool_name="memory.promote",
                    params=promote_params,
                )
            except PolicyViolationError:
                continue
            promote_result = await self._tool_execution.execute_tool_call(
                tool_call=ToolCall(name="memory.promote", params=promote_params),
                ctx=ToolContext(
                    profile_id=profile_id,
                    session_id=session_id,
                    run_id=run_id,
                    runtime_metadata=runtime_metadata,
                ),
            )
            if promote_result.ok:
                promoted_keys.append(plan.memory_key)
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="memory.auto_save",
            payload={
                "ok": True,
                "saved": len(saved_keys),
                "memory_keys": tuple(saved_keys),
                "promoted": len(promoted_keys),
                "promoted_memory_keys": tuple(promoted_keys),
            },
        )

    def _build_consolidation_plan(
        self,
        candidate: ExtractedMemoryCandidate,
    ) -> MemoryConsolidationPlan:
        return MemoryConsolidationService.plan_candidate(candidate)
