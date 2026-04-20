"""Main agent loop coordinator."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
from afkbot.repositories.pending_resume_envelope_repo import PendingResumeEnvelopeRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.browser_carryover import BrowserCarryoverService
from afkbot.services.agent_loop.chat_history_builder import ChatHistoryBuilder
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.llm_iteration_runtime import LLMIterationRuntime
from afkbot.services.agent_loop.llm_request_compaction import LLMRequestCompactionService
from afkbot.services.agent_loop.llm_request_runtime import LLMRequestRuntime
from afkbot.services.agent_loop.loop_sanitizer import (
    is_sensitive_field as _is_sensitive_field_name,
)
from afkbot.services.agent_loop.loop_sanitizer import (
    sanitize as _sanitize_text,
)
from afkbot.services.agent_loop.loop_sanitizer import (
    sanitize_value as _sanitize_nested_value,
)
from afkbot.services.agent_loop.loop_sanitizer import (
    to_params_dict as _to_params_dict_safe,
)
from afkbot.services.agent_loop.loop_sanitizer import (
    to_payload_dict as _to_payload_dict_safe,
)
from afkbot.services.agent_loop.loop_sanitizer import (
    tool_log_payload as _tool_log_payload_sanitized,
)
from afkbot.services.agent_loop.memory_runtime import MemoryRuntime
from afkbot.services.agent_loop.pending_envelopes import PendingEnvelopeBuilder
from afkbot.services.agent_loop.planning_policy import ChatPlanningMode
from afkbot.services.agent_loop.runtime_facts import TrustedRuntimeFactsService
from afkbot.services.agent_loop.runlog_runtime import RunlogRuntime
from afkbot.services.agent_loop.safety_policy import SafetyPolicy
from afkbot.services.agent_loop.security_guard import SecurityGuard
from afkbot.services.agent_loop.session_skill_affinity import SessionSkillAffinityService
from afkbot.services.agent_loop.session_compaction import SessionCompactionService
from afkbot.services.agent_loop.session_retention import SessionRetentionService
from afkbot.services.agent_loop.sessions import SessionService
from afkbot.services.agent_loop.skill_router import SkillRouter
from afkbot.services.agent_loop.tool_runtime_factory import (
    build_guarded_tool_execution_runtime,
)
from afkbot.services.agent_loop.tool_invocation_gates import ToolInvocationGuards
from afkbot.services.agent_loop.tool_exposure import ToolExposureBuilder
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.agent_loop.turn_execution import TurnExecutionRuntime
from afkbot.services.agent_loop.turn_finalizer import TurnFinalizer
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.agent_loop.turn_preparation import (
    TurnPreparationRuntime,
)
from afkbot.services.llm.contracts import LLMProvider
from afkbot.services.llm.reasoning import ThinkingLevel
from afkbot.services.memory.consolidation import get_memory_consolidation_service
from afkbot.services.llm_timeout_policy import (
    DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
    DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC,
)
from afkbot.services.policy import PolicyEngine
from afkbot.services.tools.base import ToolCall
from afkbot.services.tools.registry import ToolRegistry

_MEMORY_LOG_REDACT_FIELDS = frozenset({"content", "query", "reason", "summary", "details_md"})


class AgentLoop:
    """Deterministic async loop with optional iterative tool-calling LLM planner."""

    def __init__(
        self,
        session: AsyncSession,
        context_builder: ContextBuilder,
        *,
        tool_registry: ToolRegistry | None = None,
        llm_provider: LLMProvider | None = None,
        llm_request_timeout_sec: float = DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
        llm_max_iterations: int = DEFAULT_LLM_MAX_ITERATIONS,
        llm_default_thinking_level: ThinkingLevel = "medium",
        chat_planning_mode: ChatPlanningMode = "auto",
        llm_execution_budget_low_sec: float = 900.0,
        llm_execution_budget_medium_sec: float = 1800.0,
        llm_execution_budget_high_sec: float = 3600.0,
        llm_execution_budget_very_high_sec: float = DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC,
        llm_history_turns: int = 8,
        chat_secret_guard_enabled: bool | None = None,
        tool_timeout_default_sec: int = 15,
        tool_timeout_max_sec: int = 120,
        secure_request_ttl_sec: int = 900,
        policy_engine: PolicyEngine | None = None,
        memory_core_enabled: bool = False,
        memory_core_max_items: int = 8,
        memory_core_max_chars: int = 600,
        memory_auto_search_enabled: bool = False,
        memory_auto_search_scope_mode: Literal[
            "auto", "profile", "chat", "thread", "user_in_chat"
        ] = "chat",
        memory_auto_search_limit: int = 3,
        memory_auto_search_include_global: bool = True,
        memory_auto_search_chat_limit: int = 3,
        memory_auto_search_global_limit: int = 2,
        memory_global_fallback_enabled: bool = True,
        memory_auto_context_item_chars: int = 240,
        memory_auto_save_enabled: bool = False,
        memory_auto_save_scope_mode: Literal[
            "auto", "profile", "chat", "thread", "user_in_chat"
        ] = "chat",
        memory_auto_promote_enabled: bool = False,
        memory_auto_save_kinds: tuple[str, ...] = (
            "fact",
            "preference",
            "decision",
            "task",
            "risk",
            "note",
        ),
        memory_auto_save_max_chars: int = 1000,
        session_compaction_enabled: bool = False,
        session_compaction_trigger_turns: int = 12,
        session_compaction_keep_recent_turns: int = 6,
        session_compaction_max_chars: int = 4000,
        session_compaction_prune_raw_turns: bool = False,
        actor: Literal["main", "subagent"] = "main",
    ) -> None:
        self._session = session
        self._tool_registry = tool_registry
        self._llm_provider = llm_provider
        self._llm_max_iterations = max(1, llm_max_iterations)
        self._llm_default_thinking_level = llm_default_thinking_level
        self._chat_planning_mode = chat_planning_mode
        self._chat_secret_guard_enabled = (
            context_builder.settings.chat_secret_guard_enabled
            if chat_secret_guard_enabled is None
            else chat_secret_guard_enabled
        )
        self._tool_timeout_default_sec = tool_timeout_default_sec
        self._tool_timeout_max_sec = tool_timeout_max_sec
        self._secure_request_ttl_sec = max(60, secure_request_ttl_sec)
        self._sessions = SessionService(session)
        self._profile_repo = ProfileRepository(session)
        self._policy_repo = ProfilePolicyRepository(session)
        self._pending_resume_repo = PendingResumeEnvelopeRepository(session)
        self._pending_secure_repo = PendingSecureRequestRepository(session)
        self._run_repo = RunRepository(session)
        self._runlog_repo = RunlogRepository(session)
        self._security_guard = SecurityGuard()
        self._skill_router = SkillRouter()
        self._skill_affinity = SessionSkillAffinityService()
        self._safety_policy = SafetyPolicy()
        self._policy_engine = policy_engine or PolicyEngine(root_dir=context_builder.root_dir)
        self._tool_skill_resolver = ToolSkillResolver(
            settings=context_builder.settings,
            tool_registry=tool_registry,
        )
        self._runlog = RunlogRuntime(
            session=session,
            run_repo=self._run_repo,
            runlog_repo=self._runlog_repo,
            sanitize_value=self._sanitize_value,
            to_payload_dict=self._to_payload_dict,
        )
        self._tool_exposure = ToolExposureBuilder(
            tool_registry=tool_registry,
            policy_engine=self._policy_engine,
            tool_skill_resolver=self._tool_skill_resolver,
            tool_requires_automation_intent=self._tool_requires_automation_intent,
        )
        self._browser_carryover = BrowserCarryoverService(
            settings=context_builder.settings,
            runlog_repo=self._runlog_repo,
        )
        self._runtime_facts = TrustedRuntimeFactsService(
            settings=context_builder.settings,
        )
        self._tool_invocation_gates = ToolInvocationGuards(
            context_builder=context_builder,
            tool_skill_resolver=self._tool_skill_resolver,
            tool_requires_automation_intent=self._tool_requires_automation_intent,
            log_skill_read=self._runlog.log_skill_read_event,
        )
        self._tool_execution = build_guarded_tool_execution_runtime(
            settings=context_builder.settings,
            tool_registry=tool_registry,
            policy_engine=self._policy_engine,
            actor=actor,
            tool_timeout_default_sec=self._tool_timeout_default_sec,
            tool_timeout_max_sec=self._tool_timeout_max_sec,
            parallel_tool_max_concurrent=context_builder.settings.agent_tool_parallel_max_concurrent,
            log_event=self._runlog.log_event,
            raise_if_cancel_requested=self._runlog.raise_if_cancel_requested,
            log_skill_read=self._runlog.log_skill_read_event,
            sanitize=self._sanitize,
            sanitize_value=self._sanitize_value,
            to_params_dict=self._to_params_dict,
            tool_log_payload=self._tool_log_payload,
            tool_requires_automation_intent=self._tool_requires_automation_intent,
        )
        self._pending_envelopes = PendingEnvelopeBuilder(
            params_normalizer=self._to_params_dict,
        )
        self._session_compaction = SessionCompactionService(
            session=session,
            enabled=session_compaction_enabled,
            trigger_turns=session_compaction_trigger_turns,
            keep_recent_turns=session_compaction_keep_recent_turns,
            history_turns=llm_history_turns,
            max_chars=session_compaction_max_chars,
            llm_provider=llm_provider,
        )
        self._chat_history = ChatHistoryBuilder(
            session=session,
            history_turns=llm_history_turns,
            sanitize=self._sanitize,
            session_compaction=self._session_compaction,
        )
        self._session_retention = SessionRetentionService(
            session=session,
            prune_raw_turns=session_compaction_prune_raw_turns,
        )
        self._llm_runtime = (
            LLMRequestRuntime(
                llm_provider=llm_provider,
                llm_request_timeout_sec=llm_request_timeout_sec,
                log_event=self._runlog.log_event,
                raise_if_cancel_requested=self._runlog.raise_if_cancel_requested,
                shared_request_scope=str(context_builder.root_dir.resolve()),
                shared_request_max_parallel=context_builder.settings.llm_shared_request_max_parallel,
                shared_request_min_interval_ms=context_builder.settings.llm_shared_request_min_interval_ms,
            )
            if llm_provider is not None
            else None
        )
        self._request_compaction = LLMRequestCompactionService(
            llm_provider=llm_provider,
            max_summary_chars=session_compaction_max_chars,
            keep_recent_turns=session_compaction_keep_recent_turns,
        )
        self._memory_runtime = MemoryRuntime(
            tool_registry=tool_registry,
            policy_engine=self._policy_engine,
            tool_execution=self._tool_execution,
            log_event=self._runlog.log_event,
            auto_search_enabled=memory_auto_search_enabled,
            auto_search_scope_mode=memory_auto_search_scope_mode,
            auto_search_limit=memory_auto_search_limit,
            auto_search_include_global=memory_auto_search_include_global,
            auto_search_chat_limit=memory_auto_search_chat_limit,
            auto_search_global_limit=memory_auto_search_global_limit,
            global_fallback_enabled=memory_global_fallback_enabled,
            auto_context_item_chars=memory_auto_context_item_chars,
            auto_save_enabled=memory_auto_save_enabled,
            auto_save_scope_mode=memory_auto_save_scope_mode,
            auto_promote_enabled=memory_auto_promote_enabled,
            auto_save_kinds=memory_auto_save_kinds,
            auto_save_max_chars=memory_auto_save_max_chars,
            consolidation_service=(
                get_memory_consolidation_service(context_builder.settings)
                if memory_core_enabled
                else None
            ),
        )
        self._turn_preparation = TurnPreparationRuntime(
            context_builder=context_builder,
            chat_history=self._chat_history,
            memory_runtime=self._memory_runtime,
            safety_policy=self._safety_policy,
            skill_affinity=self._skill_affinity,
            skill_router=self._skill_router,
            tool_exposure=self._tool_exposure,
            browser_carryover=self._browser_carryover,
            runtime_facts=self._runtime_facts,
            memory_core_enabled=memory_core_enabled,
            memory_core_max_items=memory_core_max_items,
            memory_core_max_chars=memory_core_max_chars,
        )
        self._llm_iterations = (
            LLMIterationRuntime(
                llm_request_runtime=self._llm_runtime,
                tool_execution=self._tool_execution,
                pending_envelopes=self._pending_envelopes,
                request_compaction=self._request_compaction,
                tool_skill_resolver=self._tool_skill_resolver,
                log_event=self._runlog.log_event,
                log_progress=self._runlog.log_progress,
                raise_if_cancel_requested=self._runlog.raise_if_cancel_requested,
                sanitize=self._sanitize,
                sanitize_value=self._sanitize_value,
                to_params_dict=self._to_params_dict,
            )
            if self._llm_runtime is not None
            else None
        )
        self._turn_finalizer = TurnFinalizer(
            run_repo=self._run_repo,
            pending_resume_repo=self._pending_resume_repo,
            pending_secure_repo=self._pending_secure_repo,
            memory_runtime=self._memory_runtime,
            session_compaction=self._session_compaction,
            session_retention=self._session_retention,
            log_event=self._runlog.log_event,
            sanitize_value=self._sanitize_value,
            secure_request_ttl_sec=self._secure_request_ttl_sec,
        )
        self._turn_execution = TurnExecutionRuntime(
            profile_repo=self._profile_repo,
            policy_repo=self._policy_repo,
            sessions=self._sessions,
            security_guard=self._security_guard,
            turn_preparation=self._turn_preparation,
            run_repo=self._run_repo,
            runlog=self._runlog,
            tool_execution=self._tool_execution,
            pending_envelopes=self._pending_envelopes,
            llm_provider_enabled=self._llm_provider is not None,
            llm_iterations=self._llm_iterations,
            policy_engine=self._policy_engine,
            llm_max_iterations=self._llm_max_iterations,
            default_thinking_level=self._llm_default_thinking_level,
            chat_planning_mode=self._chat_planning_mode,
            llm_request_timeout_sec=llm_request_timeout_sec,
            llm_execution_budget_low_sec=llm_execution_budget_low_sec,
            llm_execution_budget_medium_sec=llm_execution_budget_medium_sec,
            llm_execution_budget_high_sec=llm_execution_budget_high_sec,
            llm_execution_budget_very_high_sec=llm_execution_budget_very_high_sec,
            turn_finalizer=self._turn_finalizer,
            chat_secret_guard_enabled=self._chat_secret_guard_enabled,
            sanitize=self._sanitize,
            sanitize_value=self._sanitize_value,
        )

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        """Execute one turn and persist run, turn, and runlog artifacts."""
        return await self._turn_execution.run_turn(
            profile_id=profile_id,
            session_id=session_id,
            message=message,
            planned_tool_calls=planned_tool_calls,
            context_overrides=context_overrides,
        )

    def _tool_requires_automation_intent(self, *, tool_name: str) -> bool:
        """Return whether tool execution must be gated behind explicit automation intent."""

        if self._tool_registry is not None:
            tool = self._tool_registry.get(tool_name)
            if tool is not None:
                return bool(getattr(tool, "requires_automation_intent", False))
        return tool_name.startswith("automation.")

    @staticmethod
    def _sanitize(value: str) -> str:
        """Mask token-like substrings for safe logging."""

        return _sanitize_text(value)

    @classmethod
    def _sanitize_value(cls, value: object, *, field_name: str | None = None) -> object:
        """Mask token-like strings in nested payloads."""

        _ = cls
        return _sanitize_nested_value(value, field_name=field_name)

    @staticmethod
    def _is_sensitive_field(field_name: str | None) -> bool:
        """Return True when field name indicates sensitive data content."""

        return _is_sensitive_field_name(field_name)

    @staticmethod
    def _to_params_dict(value: object) -> dict[str, object]:
        return _to_params_dict_safe(value)

    @staticmethod
    def _to_payload_dict(value: object) -> dict[str, object]:
        return _to_payload_dict_safe(value)

    @classmethod
    def _tool_log_payload(cls, *, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        """Build runlog payload with extra per-tool redaction rules."""

        _ = cls
        return _tool_log_payload_sanitized(
            tool_name=tool_name,
            payload=payload,
            redact_fields=_MEMORY_LOG_REDACT_FIELDS,
        )
