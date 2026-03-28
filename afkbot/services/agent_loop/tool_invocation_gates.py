"""Invocation guards for automation intent, skill gating, and subagent intent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.tools.base import ToolResult

SkillReadLogger = Callable[..., Awaitable[None]]


class ToolInvocationGuards:
    """Resolve deterministic gate failures before tool execution."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        tool_skill_resolver: ToolSkillResolver,
        tool_requires_automation_intent: Callable[..., bool],
        log_skill_read: SkillReadLogger,
    ) -> None:
        self._context_builder = context_builder
        self._tool_skill_resolver = tool_skill_resolver
        self._tool_requires_automation_intent = tool_requires_automation_intent
        self._log_skill_read = log_skill_read

    def automation_intent_required_result(
        self,
        *,
        tool_name: str,
        automation_intent: bool,
    ) -> ToolResult | None:
        """Return deterministic error when automation-only tool is used without explicit intent."""

        if automation_intent or not self._tool_requires_automation_intent(tool_name=tool_name):
            return None
        return ToolResult.error(
            error_code="automation_intent_required",
            reason=(
                "Automation tools are allowed only for explicit automation "
                "requests (cron/schedule/webhook/create/list/get/update/delete)."
            ),
        )

    def subagent_intent_mismatch_result(
        self,
        *,
        requested_subagent: str,
        explicit_skills: set[str],
        explicit_subagents: set[str],
    ) -> ToolResult | None:
        """Return mismatch error when runtime intent does not allow current subagent call."""

        if explicit_skills and not explicit_subagents:
            return ToolResult.error(
                error_code="subagent_intent_mismatch",
                reason=(
                    "subagent.run is blocked: user explicitly requested a skill, "
                    "not a subagent."
                ),
            )
        if explicit_subagents and requested_subagent not in explicit_subagents:
            return ToolResult.error(
                error_code="subagent_intent_mismatch",
                reason=(
                    "subagent.run must use explicitly requested subagent name. "
                    f"Expected one of: {', '.join(sorted(explicit_subagents))}."
                ),
            )
        return None

    async def skill_gate_result(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        tool_name: str,
        params: dict[str, object],
    ) -> ToolResult | None:
        """Return skill-gate result or log required skill read before execution."""

        required_skills = self._tool_skill_resolver.required_skills_for_tool_call(
            tool_name=tool_name,
            params=params,
            profile_id=profile_id,
        )
        if not required_skills:
            return None
        required_skill = self._tool_skill_resolver.required_skill_for_tool(
            tool_name=tool_name,
            params=params,
            profile_id=profile_id,
        )
        if required_skill is None:
            expected = ", ".join(sorted(required_skills))
            metadata: dict[str, object] = {"required_skills": sorted(required_skills)}
            if len(required_skills) == 1:
                metadata["required_skill"] = next(iter(required_skills))
            return ToolResult.error(
                error_code="tool_requires_skill",
                reason=(
                    f"Tool {tool_name} requires one routed skill from: {expected}. "
                    "Select the correct integration or explicit skill so runtime can load the matching SKILL.md."
                ),
                metadata=metadata,
            )
        try:
            skill_body = await self._context_builder.load_skill(
                name=required_skill,
                profile_id=profile_id,
            )
        except (FileNotFoundError, ValueError):
            return None
        await self._log_skill_read(
            run_id=run_id,
            session_id=session_id,
            skill_name=required_skill,
            tool_name=tool_name,
            size=len(skill_body),
        )
        return None
