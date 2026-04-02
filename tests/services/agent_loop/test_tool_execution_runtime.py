"""Focused regression tests for guarded tool execution ordering."""

from __future__ import annotations

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
from afkbot.services.tools.base import ToolBase, ToolCall, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters


async def _noop_async(**_: object) -> None:
    """Provide one deterministic async no-op for runtime boundaries."""

    return None


class _FakeSecurityGuard:
    def guard_tool_call(self, *, call: ToolCall):
        class _GuardedCall:
            allow = True
            error_code = None
            blocked_reason = None
            log_call = call
            execution_call = call

        return _GuardedCall()


class _FakePolicyEngine:
    def ensure_tool_call_allowed(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
        approved_tool_names: set[str] | None = None,
    ) -> None:
        _ = policy, tool_name, params, approved_tool_names
        return None


class _FakeSafetyPolicy:
    def extract_confirmation_ack(
        self,
        params: dict[str, object],
    ) -> tuple[bool, str | None]:
        _ = params
        return False, None

    def approval_required_result(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
        confirmed: bool,
        question_id: str | None,
    ) -> ToolResult | None:
        _ = policy, tool_name, params, confirmed, question_id
        return None


class _FakeToolInvocationGuards:
    def subagent_intent_mismatch_result(
        self,
        *,
        requested_subagent: str,
        explicit_skills: set[str],
        explicit_subagents: set[str],
    ) -> ToolResult | None:
        _ = requested_subagent, explicit_skills, explicit_subagents
        return None

    def automation_intent_required_result(
        self,
        *,
        tool_name: str,
        automation_intent: bool,
    ) -> ToolResult | None:
        _ = tool_name, automation_intent
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
        _ = run_id, session_id, profile_id, tool_name, params
        return None


class _ExplodingPolicyParamsTool(ToolBase):
    name = "mcp.github.search"
    description = "Exploding policy params tool"

    def policy_params(
        self,
        raw_params: dict[str, object],
        *,
        ctx: ToolContext | None = None,
    ) -> dict[str, object]:
        _ = raw_params, ctx
        raise AssertionError("policy_params should not run for disallowed tools")

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        _ = ctx, params
        raise AssertionError("execute should not run for disallowed tools")


class _FakeRegistry:
    def __init__(self, tool: ToolBase) -> None:
        self._tool = tool

    def get(self, tool_name: str) -> ToolBase | None:
        if tool_name == self._tool.name:
            return self._tool
        return None

    def list_names(self) -> tuple[str, ...]:
        return (self._tool.name,)


async def test_execute_requested_tool_calls_rejects_disallowed_tool_before_policy_params() -> None:
    """Disallowed tools should fail before policy-parameter expansion runs."""

    # Arrange
    runtime = ToolExecutionRuntime(
        tool_registry=_FakeRegistry(_ExplodingPolicyParamsTool()),
        actor="main",
        policy_engine=_FakePolicyEngine(),
        security_guard=_FakeSecurityGuard(),
        safety_policy=_FakeSafetyPolicy(),
        tool_invocation_gates=_FakeToolInvocationGuards(),
        tool_timeout_default_sec=30,
        tool_timeout_max_sec=60,
        log_event=_noop_async,
        raise_if_cancel_requested=_noop_async,
        sanitize=lambda value: value,
        sanitize_value=lambda value: value,
        to_params_dict=lambda value: dict(value),
        tool_log_payload=lambda **_: {},
    )

    # Act
    results = await runtime.execute_requested_tool_calls(
        run_id=1,
        session_id="s-disallowed",
        profile_id="default",
        tool_calls=[ToolCall(name="mcp.github.search", params={})],
        policy=ProfilePolicy(profile_id="default"),
        automation_intent=False,
        explicit_skill_requests=None,
        explicit_subagent_requests=None,
        allow_confirmation_markers=False,
        allowed_tool_names={"debug.echo"},
    )

    # Assert
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error_code == "tool_not_allowed_in_turn"
    assert results[0].reason == "Tool not available in current turn: mcp.github.search"


class _PolicyCaptureEngine(_FakePolicyEngine):
    def __init__(self) -> None:
        self.calls: list[tuple[str, set[str] | None]] = []

    def ensure_tool_call_allowed(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
        approved_tool_names: set[str] | None = None,
    ) -> None:
        _ = policy, params
        self.calls.append((tool_name, None if approved_tool_names is None else set(approved_tool_names)))


class _EchoTool(ToolBase):
    name = "bash.exec"
    description = "Echo tool"

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        _ = ctx, params
        return ToolResult(ok=True, payload={"ok": True})


async def test_execute_requested_tool_calls_passes_cli_policy_tool_approval_override() -> None:
    """CLI-approved tools should bypass only the policy allow gate for that tool."""

    policy_engine = _PolicyCaptureEngine()
    runtime = ToolExecutionRuntime(
        tool_registry=_FakeRegistry(_EchoTool()),
        actor="main",
        policy_engine=policy_engine,
        security_guard=_FakeSecurityGuard(),
        safety_policy=_FakeSafetyPolicy(),
        tool_invocation_gates=_FakeToolInvocationGuards(),
        tool_timeout_default_sec=30,
        tool_timeout_max_sec=60,
        log_event=_noop_async,
        raise_if_cancel_requested=_noop_async,
        sanitize=lambda value: value,
        sanitize_value=lambda value: value,
        to_params_dict=lambda value: dict(value),
        tool_log_payload=lambda **_: {},
    )

    results = await runtime.execute_requested_tool_calls(
        run_id=1,
        session_id="s-policy-override",
        profile_id="default",
        tool_calls=[ToolCall(name="bash.exec", params={})],
        policy=ProfilePolicy(profile_id="default"),
        automation_intent=False,
        explicit_skill_requests=None,
        explicit_subagent_requests=None,
        allow_confirmation_markers=False,
        allowed_tool_names={"bash.exec"},
        approved_tool_names={"bash.exec"},
    )

    assert len(results) == 1
    assert results[0].ok is True
    assert policy_engine.calls == [("bash.exec", {"bash.exec"})]
