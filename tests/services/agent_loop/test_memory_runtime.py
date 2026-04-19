"""Tests for the scope-aware auto-memory runtime hooks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.memory_runtime import MemoryRuntime
from afkbot.services.tools.base import ToolCall, ToolContext, ToolResult

AsyncLogEvent = Callable[..., Awaitable[None]]


class _FakeRegistry:
    def __init__(self, *tool_names: str) -> None:
        self._tool_names = set(tool_names)

    def get(self, tool_name: str) -> object | None:
        if tool_name in self._tool_names:
            return object()
        return None


class _AllowAllPolicyEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def ensure_tool_call_allowed(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
    ) -> None:
        _ = policy
        self.calls.append((tool_name, params))


class _FakeToolExecution:
    def __init__(self, *results: ToolResult) -> None:
        self._results = list(results)
        self.calls: list[tuple[ToolCall, ToolContext]] = []

    async def execute_tool_call(self, *, tool_call: ToolCall, ctx: ToolContext) -> ToolResult:
        self.calls.append((tool_call, ctx))
        return self._results.pop(0)


class _FakeConsolidationService:
    def __init__(
        self,
        *,
        result: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._result = {"ok": True} if result is None else result
        self._error = error

    async def mirror_plan_to_core(self, **kwargs: object) -> object | None:
        self.calls.append(dict(kwargs))
        if self._error is not None:
            raise self._error
        return self._result


async def _collect_log_event(storage: list[dict[str, object]], **kwargs: object) -> None:
    storage.append(dict(kwargs))


def _runtime_metadata() -> dict[str, object]:
    return {
        "transport": "telegram_user",
        "account_id": "personal-user",
        "peer_id": "100",
        "channel_binding": {"binding_id": "personal-user", "session_policy": "per-chat"},
    }


def _watcher_runtime_metadata() -> dict[str, object]:
    return {
        "transport": "telegram_user",
        "account_id": "personal-user",
        "peer_id": "__watcher__:telethon-main",
        "telethon_watcher": {
            "endpoint_id": "telethon-main",
            "event_count": 2,
        },
    }


async def test_memory_runtime_compacts_search_results() -> None:
    """Auto search should query scoped memory and compact results into runtime metadata."""

    policy = ProfilePolicy(profile_id="default")
    events: list[dict[str, object]] = []
    execution = _FakeToolExecution(
        ToolResult(
            ok=True,
            payload={
                "items": [
                    {
                        "memory_key": "nick",
                        "summary": "Chat fact: user nickname is Nikita and prefers long context.",
                        "memory_kind": "fact",
                        "scope_kind": "chat",
                        "visibility": "local",
                        "score": 0.2,
                        "source_kind": "auto",
                    },
                    {"memory_key": "empty", "summary": "   "},
                ]
            },
        )
    )
    runtime = MemoryRuntime(
        tool_registry=_FakeRegistry("memory.search"),
        policy_engine=_AllowAllPolicyEngine(),
        tool_execution=execution,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        auto_search_enabled=True,
        auto_search_scope_mode="chat",
        auto_search_limit=2,
        auto_search_include_global=True,
        auto_search_chat_limit=2,
        auto_search_global_limit=1,
        global_fallback_enabled=True,
        auto_context_item_chars=18,
        auto_save_enabled=False,
        auto_save_scope_mode="chat",
        auto_promote_enabled=False,
        auto_save_kinds=("fact", "preference"),
        auto_save_max_chars=100,
    )

    metadata = await runtime.auto_search_metadata(
        run_id=11,
        session_id="s-1",
        profile_id="default",
        user_message="What nickname do I use?",
        policy=policy,
        runtime_metadata=_runtime_metadata(),
    )

    assert metadata == {
        "auto_memory": [
            {
                "memory_key": "nick",
                    "summary": "Chat fact: user nickname is Niki",
                "score": 0.2,
                "memory_kind": "fact",
                "scope_kind": "chat",
                "visibility": "local",
                "source_kind": "auto",
            }
        ]
    }
    assert execution.calls
    tool_call, ctx = execution.calls[0]
    assert tool_call.name == "memory.search"
    assert tool_call.params["scope"] == "chat"
    assert tool_call.params["include_global"] is True
    assert ctx.run_id == 11
    assert ctx.runtime_metadata == _runtime_metadata()
    assert events == [
        {
            "run_id": 11,
            "session_id": "s-1",
            "event_type": "memory.auto_search",
            "payload": {"ok": True, "hits": 1, "scope_kind": "chat", "include_global": True},
        }
    ]


async def test_memory_runtime_auto_save_extracts_structured_fact() -> None:
    """Auto save should persist extracted structured memory records, not transcript blobs."""

    policy = ProfilePolicy(profile_id="default")
    events: list[dict[str, object]] = []
    execution = _FakeToolExecution(ToolResult(ok=True, payload={"item": {"memory_key": "saved"}}))
    runtime = MemoryRuntime(
        tool_registry=_FakeRegistry("memory.upsert"),
        policy_engine=_AllowAllPolicyEngine(),
        tool_execution=execution,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        auto_search_enabled=False,
        auto_search_scope_mode="chat",
        auto_search_limit=2,
        auto_search_include_global=True,
        auto_search_chat_limit=2,
        auto_search_global_limit=1,
        global_fallback_enabled=True,
        auto_context_item_chars=64,
        auto_save_enabled=True,
        auto_save_scope_mode="chat",
        auto_promote_enabled=False,
        auto_save_kinds=("fact", "preference"),
        auto_save_max_chars=120,
    )

    await runtime.auto_save_turn(
        run_id=12,
        session_id="s-2",
        profile_id="default",
        user_message="Отвечай в этом чате коротко и по-русски",
        assistant_message="Принял. В этом чате буду отвечать коротко и по-русски.",
        action="finalize",
        policy=policy,
        runtime_metadata=_runtime_metadata(),
    )

    assert execution.calls
    tool_call, ctx = execution.calls[0]
    assert tool_call.name == "memory.upsert"
    assert str(tool_call.params["source"]) == "agent_loop.auto"
    assert str(tool_call.params["scope"]) == "chat"
    assert str(tool_call.params["memory_kind"]) == "preference"
    assert "user:" not in str(tool_call.params.get("details_md"))
    assert ctx.run_id == 12
    assert ctx.runtime_metadata == _runtime_metadata()
    assert events == [
        {
            "run_id": 12,
            "session_id": "s-2",
            "event_type": "memory.auto_save",
            "payload": {
                "ok": True,
                "saved": 1,
                "memory_keys": (str(tool_call.params["memory_key"]),),
                "promoted": 0,
                "promoted_memory_keys": (),
            },
        }
    ]


async def test_memory_runtime_auto_search_uses_local_chat_scope_for_watcher_digest() -> None:
    """Watcher digest turns should still resolve local chat-scoped memory instead of profile-global memory."""

    policy = ProfilePolicy(profile_id="default")
    events: list[dict[str, object]] = []
    execution = _FakeToolExecution(ToolResult(ok=True, payload={"items": []}))
    runtime = MemoryRuntime(
        tool_registry=_FakeRegistry("memory.search"),
        policy_engine=_AllowAllPolicyEngine(),
        tool_execution=execution,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        auto_search_enabled=True,
        auto_search_scope_mode="auto",
        auto_search_limit=2,
        auto_search_include_global=True,
        auto_search_chat_limit=2,
        auto_search_global_limit=1,
        global_fallback_enabled=True,
        auto_context_item_chars=32,
        auto_save_enabled=False,
        auto_save_scope_mode="auto",
        auto_promote_enabled=False,
        auto_save_kinds=("fact",),
        auto_save_max_chars=100,
    )

    await runtime.auto_search_metadata(
        run_id=13,
        session_id="telegram_user_watch:telethon-main",
        profile_id="default",
        user_message="Что важного было в watched chats?",
        policy=policy,
        runtime_metadata=_watcher_runtime_metadata(),
    )

    assert execution.calls
    tool_call, ctx = execution.calls[0]
    assert tool_call.params["scope"] == "auto"
    assert ctx.runtime_metadata == _watcher_runtime_metadata()
    assert events == [
        {
            "run_id": 13,
            "session_id": "telegram_user_watch:telethon-main",
            "event_type": "memory.auto_search",
            "payload": {"ok": True, "hits": 0, "scope_kind": "chat", "include_global": True},
        }
    ]


async def test_memory_runtime_consolidation_mirrors_profile_worthy_preference() -> None:
    """Profile-worthy extracted plans should be mirrored into core memory via consolidator."""

    policy = ProfilePolicy(profile_id="default")
    events: list[dict[str, object]] = []
    execution = _FakeToolExecution(ToolResult(ok=True, payload={"item": {"memory_key": "saved"}}))
    profile_memory = _FakeConsolidationService()
    runtime = MemoryRuntime(
        tool_registry=_FakeRegistry("memory.upsert"),
        policy_engine=_AllowAllPolicyEngine(),
        tool_execution=execution,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        auto_search_enabled=False,
        auto_search_scope_mode="chat",
        auto_search_limit=2,
        auto_search_include_global=True,
        auto_search_chat_limit=2,
        auto_search_global_limit=1,
        global_fallback_enabled=True,
        auto_context_item_chars=64,
        auto_save_enabled=True,
        auto_save_scope_mode="chat",
        auto_promote_enabled=False,
        auto_save_kinds=("fact", "preference", "decision"),
        auto_save_max_chars=200,
        consolidation_service=profile_memory,  # type: ignore[arg-type]
    )

    await runtime.auto_save_turn(
        run_id=14,
        session_id="s-3",
        profile_id="default",
        user_message="По умолчанию отвечай по-русски и кратко.",
        assistant_message="Принял. По умолчанию буду отвечать по-русски и кратко.",
        action="finalize",
        policy=policy,
        runtime_metadata=_runtime_metadata(),
    )

    assert profile_memory.calls
    assert profile_memory.calls[0]["plan"].core_memory_key == "preferred_language"
    assert profile_memory.calls[0]["plan"].memory_kind == "preference"


async def test_memory_runtime_does_not_mirror_profile_memory_when_upsert_fails() -> None:
    """Core-memory mirroring should only happen after archival save succeeds."""

    policy = ProfilePolicy(profile_id="default")
    execution = _FakeToolExecution(ToolResult(ok=False, error_code="boom", reason="nope"))
    profile_memory = _FakeConsolidationService()
    runtime = MemoryRuntime(
        tool_registry=_FakeRegistry("memory.upsert"),
        policy_engine=_AllowAllPolicyEngine(),
        tool_execution=execution,
        log_event=lambda **kwargs: _collect_log_event([], **kwargs),
        auto_search_enabled=False,
        auto_search_scope_mode="chat",
        auto_search_limit=2,
        auto_search_include_global=True,
        auto_search_chat_limit=2,
        auto_search_global_limit=1,
        global_fallback_enabled=True,
        auto_context_item_chars=64,
        auto_save_enabled=True,
        auto_save_scope_mode="chat",
        auto_promote_enabled=False,
        auto_save_kinds=("fact", "preference", "decision"),
        auto_save_max_chars=200,
        consolidation_service=profile_memory,  # type: ignore[arg-type]
    )

    await runtime.auto_save_turn(
        run_id=15,
        session_id="s-4",
        profile_id="default",
        user_message="По умолчанию отвечай по-русски и кратко.",
        assistant_message="Принял.",
        action="finalize",
        policy=policy,
        runtime_metadata=_runtime_metadata(),
    )

    assert profile_memory.calls == []


async def test_memory_runtime_profile_mirror_failure_does_not_break_auto_save() -> None:
    """Secondary core-memory mirror failures must not fail the turn."""

    policy = ProfilePolicy(profile_id="default")
    events: list[dict[str, object]] = []
    execution = _FakeToolExecution(ToolResult(ok=True, payload={"item": {"memory_key": "saved"}}))
    profile_memory = _FakeConsolidationService(error=RuntimeError("mirror failed"))
    runtime = MemoryRuntime(
        tool_registry=_FakeRegistry("memory.upsert"),
        policy_engine=_AllowAllPolicyEngine(),
        tool_execution=execution,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        auto_search_enabled=False,
        auto_search_scope_mode="chat",
        auto_search_limit=2,
        auto_search_include_global=True,
        auto_search_chat_limit=2,
        auto_search_global_limit=1,
        global_fallback_enabled=True,
        auto_context_item_chars=64,
        auto_save_enabled=True,
        auto_save_scope_mode="chat",
        auto_promote_enabled=False,
        auto_save_kinds=("fact", "preference", "decision"),
        auto_save_max_chars=200,
        consolidation_service=profile_memory,  # type: ignore[arg-type]
    )

    await runtime.auto_save_turn(
        run_id=16,
        session_id="s-5",
        profile_id="default",
        user_message="По умолчанию отвечай по-русски и кратко.",
        assistant_message="Принял.",
        action="finalize",
        policy=policy,
        runtime_metadata=_runtime_metadata(),
    )

    assert profile_memory.calls
    assert events[-1]["event_type"] == "memory.auto_save"
    assert events[-1]["payload"]["saved"] == 1
