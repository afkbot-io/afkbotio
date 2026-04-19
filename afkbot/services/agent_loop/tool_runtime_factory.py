"""Shared builders for guarded tool execution runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from afkbot.services.policy import PolicyEngine
from afkbot.settings import Settings

if TYPE_CHECKING:
    from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
    from afkbot.services.tools.registry import ToolRegistry

AsyncLogEvent = Callable[..., Awaitable[None]]
AsyncCancelCheck = Callable[..., Awaitable[None]]
SanitizeValue = Callable[[object], object]
NormalizeParams = Callable[[object], dict[str, object]]
BuildToolLogPayload = Callable[..., dict[str, object]]
SanitizeText = Callable[[str], str]
SkillReadLogger = Callable[..., Awaitable[None]]


def build_guarded_tool_execution_runtime(
    *,
    settings: Settings,
    tool_registry: ToolRegistry | None,
    policy_engine: PolicyEngine,
    actor: Literal["main", "subagent"] = "main",
    tool_timeout_default_sec: int,
    tool_timeout_max_sec: int,
    parallel_tool_max_concurrent: int,
    log_event: AsyncLogEvent,
    raise_if_cancel_requested: AsyncCancelCheck,
    log_skill_read: SkillReadLogger,
    sanitize: SanitizeText,
    sanitize_value: SanitizeValue,
    to_params_dict: NormalizeParams,
    tool_log_payload: BuildToolLogPayload,
    tool_requires_automation_intent: Callable[..., bool],
) -> ToolExecutionRuntime:
    """Build the canonical guarded tool execution runtime for one runtime surface."""

    from afkbot.services.agent_loop.context_builder import ContextBuilder
    from afkbot.services.agent_loop.safety_policy import SafetyPolicy
    from afkbot.services.agent_loop.security_guard import SecurityGuard
    from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
    from afkbot.services.agent_loop.tool_invocation_gates import ToolInvocationGuards
    from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
    from afkbot.services.skills.skills import SkillLoader

    context_builder = ContextBuilder(settings, SkillLoader(settings))
    tool_skill_resolver = ToolSkillResolver(settings=settings, tool_registry=tool_registry)
    tool_invocation_gates = ToolInvocationGuards(
        context_builder=context_builder,
        tool_skill_resolver=tool_skill_resolver,
        tool_requires_automation_intent=tool_requires_automation_intent,
        log_skill_read=log_skill_read,
    )
    return ToolExecutionRuntime(
        tool_registry=tool_registry,
        actor=actor,
        policy_engine=policy_engine,
        security_guard=SecurityGuard(),
        safety_policy=SafetyPolicy(),
        tool_invocation_gates=tool_invocation_gates,
        tool_timeout_default_sec=tool_timeout_default_sec,
        tool_timeout_max_sec=tool_timeout_max_sec,
        parallel_tool_max_concurrent=parallel_tool_max_concurrent,
        log_event=log_event,
        raise_if_cancel_requested=raise_if_cancel_requested,
        sanitize=sanitize,
        sanitize_value=sanitize_value,
        to_params_dict=to_params_dict,
        tool_log_payload=tool_log_payload,
    )
