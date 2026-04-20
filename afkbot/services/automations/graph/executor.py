"""Execution runtime for automation graph mode."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.models.automation import Automation
from afkbot.models.automation_edge import AutomationEdge
from afkbot.models.automation_flow import AutomationFlow
from afkbot.models.automation_node import AutomationNode
from afkbot.models.automation_node_version import AutomationNodeVersion
from afkbot.repositories.automation_graph_repo import AutomationGraphRepository
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.services.agent_loop.loop_sanitizer import sanitize as _loop_sanitize
from afkbot.services.agent_loop.loop_sanitizer import sanitize_value as _loop_sanitize_value
from afkbot.services.agent_loop.loop_sanitizer import to_params_dict as _loop_to_params_dict
from afkbot.services.agent_loop.loop_sanitizer import tool_log_payload as _loop_tool_log_payload
from afkbot.services.agent_loop.security_guard import SecurityGuard
from afkbot.services.agent_loop.tool_runtime_factory import (
    build_guarded_tool_execution_runtime,
)
from afkbot.services.automations.payloads import sanitize_payload_value
from afkbot.services.automations.session_runner_factory import (
    AutomationSessionRunnerFactory,
    build_automation_session_runner,
)
from afkbot.services.automations.graph.node_registry import (
    AutomationGraphNodeAdapterRegistry,
)
from afkbot.services.automations.graph.os_sandbox import (
    OSSandboxUnavailableError,
    build_code_node_launch,
)
from afkbot.services.policy import PolicyEngine
from afkbot.services.tools.base import ToolBase, ToolCall, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParametersValidationError
from afkbot.settings import Settings

if TYPE_CHECKING:
    from afkbot.models.profile_policy import ProfilePolicy
    from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides
    from afkbot.services.subagents.contracts import (
        SubagentResultResponse,
        SubagentRunAccepted,
        SubagentWaitResponse,
    )

class _ToolLookup(Protocol):
    """Minimal lookup surface needed for automation-intent policy checks."""

    def get(self, name: str) -> ToolBase | None: ...


class SubagentServiceProtocol(Protocol):
    """Runtime contract required by graph agent nodes."""

    async def run(
        self,
        *,
        ctx: ToolContext,
        prompt: str,
        subagent_name: str | None,
        timeout_sec: int | None,
    ) -> "SubagentRunAccepted": ...

    async def wait(
        self,
        *,
        task_id: str,
        timeout_sec: int | None,
        profile_id: str,
        session_id: str,
    ) -> "SubagentWaitResponse": ...

    async def result(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> "SubagentResultResponse": ...

    async def cancel(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> "SubagentResultResponse": ...

    async def shutdown(self) -> None: ...


AutomationGraphSubagentFactory = Callable[[Settings], SubagentServiceProtocol]


@dataclass(frozen=True, slots=True)
class LoadedNodeSchemas:
    """Prevalidated schemas attached to one loaded graph node."""

    input_schema: Mapping[str, object] | None = None
    output_schema: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LoadedGraph:
    """Loaded active graph snapshot with versioned node artifacts."""

    automation: Automation
    flow: AutomationFlow
    nodes: tuple[AutomationNode, ...]
    edges: tuple[AutomationEdge, ...]
    versions_by_id: Mapping[int, AutomationNodeVersion]
    schemas_by_node_id: Mapping[int, LoadedNodeSchemas] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphExecutionContext:
    """Runtime context shared with all node adapters in one run."""

    run_id: int
    automation_id: int
    profile_id: str
    automation_prompt: str
    trigger_type: Literal["cron", "webhook"]
    trigger_payload: Mapping[str, object]
    parent_session_id: str | None
    context_overrides: TurnContextOverrides | None
    event_hash: str | None
    cron_expr: str | None
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    session_runner_factory: AutomationSessionRunnerFactory | None
    subagent_service_factory: AutomationGraphSubagentFactory | None
    timeout_sec: float | None


@dataclass(frozen=True, slots=True)
class NodeInvocation:
    """One concrete node execution request."""

    node: AutomationNode
    config: Mapping[str, object]
    inputs: Mapping[str, object]
    version: AutomationNodeVersion | None
    context: GraphExecutionContext

    def default_input(self) -> object | None:
        value = self.inputs.get("default")
        if value is not None:
            return value
        if not self.inputs:
            return None
        return next(iter(self.inputs.values()))


@dataclass(frozen=True, slots=True)
class GraphNodeEffect:
    """Structured effect emitted by one node execution attempt."""

    effect_kind: str
    safety_class: Literal["safe", "unsafe"]
    committed: bool
    idempotency_key: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NodeAdapterResult:
    """Canonical adapter outcome normalized for executor bookkeeping."""

    ok: bool
    ports: Mapping[str, object] = field(default_factory=dict)
    selected_ports: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    effects: tuple[GraphNodeEffect, ...] = ()
    error_code: str | None = None
    reason: str | None = None
    unsafe_side_effects: bool = False

    @classmethod
    def success(
        cls,
        *,
        ports: Mapping[str, object],
        selected_ports: tuple[str, ...] | None = None,
        metadata: Mapping[str, object] | None = None,
        effects: tuple[GraphNodeEffect, ...] | None = None,
        unsafe_side_effects: bool = False,
    ) -> "NodeAdapterResult":
        normalized_effects = tuple(effects or ())
        return cls(
            ok=True,
            ports=ports,
            selected_ports=tuple(selected_ports or tuple(ports.keys())),
            metadata={} if metadata is None else metadata,
            effects=normalized_effects,
            unsafe_side_effects=unsafe_side_effects or _has_unsafe_effects(normalized_effects),
        )

    @classmethod
    def failure(
        cls,
        *,
        error_code: str,
        reason: str,
        metadata: Mapping[str, object] | None = None,
        effects: tuple[GraphNodeEffect, ...] | None = None,
        unsafe_side_effects: bool = False,
    ) -> "NodeAdapterResult":
        normalized_effects = tuple(effects or ())
        return cls(
            ok=False,
            error_code=error_code,
            reason=reason,
            metadata={} if metadata is None else metadata,
            effects=normalized_effects,
            unsafe_side_effects=unsafe_side_effects or _has_unsafe_effects(normalized_effects),
        )


@dataclass(slots=True)
class PendingNodeState:
    """Mutable execution state for one node during topological traversal."""

    incoming_total: int
    resolved_incoming: int = 0
    delivered_incoming: int = 0
    inputs: dict[str, list[object]] = field(default_factory=dict)
    ready_enqueued: bool = False
    finalized: bool = False


@dataclass(frozen=True, slots=True)
class GraphExecutionOutcome:
    """Terminal outcome returned to graph service after execution."""

    status: Literal["succeeded", "failed"]
    final_output: dict[str, object] | None
    unsafe_side_effects: bool
    error_code: str | None = None
    reason: str | None = None


class TriggerInputNodeAdapter:
    """Expose the raw trigger payload as the graph entry payload."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        return NodeAdapterResult.success(ports={"default": dict(invocation.context.trigger_payload)})


class PassthroughNodeAdapter:
    """Return one input port unchanged."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        source_port = str(invocation.config.get("source_port") or "default")
        payload = invocation.inputs.get(source_port)
        if payload is None and invocation.inputs:
            payload = next(iter(invocation.inputs.values()))
        return NodeAdapterResult.success(ports={"default": payload})


class SwitchValueNodeAdapter:
    """Route payload to one named port based on a dotted-path value."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        payload = invocation.default_input()
        path = str(invocation.config.get("path") or "").strip()
        value = _resolve_path(payload, path) if path else payload
        raw_cases = invocation.config.get("cases")
        cases_payload = raw_cases if isinstance(raw_cases, Mapping) else {}
        cases = {
            str(key): str(inner)
            for key, inner in cases_payload.items()
        }
        selected_port = cases.get(str(value))
        default_port = str(invocation.config.get("default_port") or "").strip()
        if not selected_port:
            if cases:
                selected_port = default_port or str(value or "default")
            else:
                selected_port = str(value or default_port or "default")
        return NodeAdapterResult.success(
            ports={selected_port: payload},
            selected_ports=(selected_port,),
        )


class ErrorRaiseNodeAdapter:
    """Fail deterministically with a structured graph-node error."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        reason = str(invocation.config.get("reason") or "graph node failed")
        return NodeAdapterResult.failure(error_code="graph_node_failed", reason=reason)


class CodePythonNodeAdapter:
    """Execute one code node inside a short-lived isolated Python subprocess."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        version = invocation.version
        if version is None or not (version.source_code or "").strip():
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="code node requires one versioned source artifact",
            )
        worker_path = Path(__file__).with_name("code_worker.py")
        request = {
            "context": {
                "run_id": invocation.context.run_id,
                "automation_id": invocation.context.automation_id,
                "node_key": invocation.node.node_key,
                "trigger_type": invocation.context.trigger_type,
                "event_hash": invocation.context.event_hash,
                "cron_expr": invocation.context.cron_expr,
            },
            "inputs": dict(invocation.inputs),
            "config": dict(invocation.config),
            "sandbox": {
                "cpu_time_sec": invocation.context.settings.automation_graph_code_cpu_time_sec,
                "memory_limit_bytes": (
                    invocation.context.settings.automation_graph_code_memory_limit_mb * 1024 * 1024
                ),
                "max_open_files": invocation.context.settings.automation_graph_code_max_open_files,
                "max_file_size_bytes": (
                    invocation.context.settings.automation_graph_code_max_file_size_bytes
                ),
            },
        }
        io_limit = invocation.context.settings.automation_graph_code_max_io_bytes
        request_bytes = json.dumps(request, ensure_ascii=True).encode("utf-8")
        if len(request_bytes) > io_limit:
            return NodeAdapterResult.failure(
                error_code="graph_node_resource_limit",
                reason=f"Code node request exceeded {io_limit} bytes",
            )
        with tempfile.TemporaryDirectory(prefix="afkbot-graph-node-") as temp_dir:
            source_path = Path(temp_dir) / "node_impl.py"
            source_path.write_text(version.source_code or "", encoding="utf-8")
            try:
                launch = build_code_node_launch(
                    base_argv=(sys.executable, "-I", "-B", str(worker_path), str(source_path)),
                    sandbox_root=Path(temp_dir),
                    explicit_read_roots=(worker_path.parent,),
                    settings=invocation.context.settings,
                )
            except OSSandboxUnavailableError as exc:
                return NodeAdapterResult.failure(
                    error_code="graph_node_sandbox_unavailable",
                    reason=str(exc),
                )
            process = await asyncio.create_subprocess_exec(
                *launch.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=temp_dir,
                env={
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                start_new_session=True,
            )
            timeout_sec = max(0.001, float(invocation.context.timeout_sec or 30.0))
            try:
                stdout, stderr = await asyncio.wait_for(
                    _communicate_limited(
                        process=process,
                        stdin_bytes=request_bytes,
                        io_limit=io_limit,
                    ),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                _kill_process_group(process)
                await process.wait()
                return NodeAdapterResult.failure(
                    error_code="graph_node_timeout",
                    reason=f"Code node timed out after {timeout_sec:.3f}s",
                )
            except _StreamLimitExceeded as exc:
                _kill_process_group(process)
                await process.wait()
                return NodeAdapterResult.failure(
                    error_code="graph_node_resource_limit",
                    reason=str(exc),
                )
        if process.returncode != 0:
            reason = (stderr.decode("utf-8", errors="replace").strip() or "code node failed")
            return NodeAdapterResult.failure(
                error_code="graph_node_failed",
                reason=_sanitize_reason_text(reason),
            )
        try:
            parsed = json.loads(stdout.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid_output",
                reason=f"Code node emitted invalid JSON: {exc}",
            )
        unsafe = bool(parsed.get("unsafe_side_effects", False)) or bool(
            invocation.config.get("unsafe_side_effects", False)
        )
        if not parsed.get("ok", False):
            return NodeAdapterResult.failure(
                error_code=str(parsed.get("error_code") or "graph_node_failed"),
                reason=_sanitize_reason_text(str(parsed.get("reason") or "graph node failed")),
                metadata=_normalize_json_mapping(parsed.get("metadata")),
                effects=_normalize_effects_payload(
                    parsed.get("effects"),
                    fallback_unsafe=unsafe,
                    fallback_kind="code.execution",
                    fallback_metadata={"node_key": invocation.node.node_key},
                ),
                unsafe_side_effects=unsafe,
            )
        ports = _normalize_json_mapping(parsed.get("ports"))
        selected_ports = tuple(str(item) for item in parsed.get("selected_ports", tuple(ports.keys())))
        metadata = _normalize_json_mapping(parsed.get("metadata"))
        return NodeAdapterResult.success(
            ports=ports,
            selected_ports=selected_ports,
            metadata=metadata,
            effects=_normalize_effects_payload(
                parsed.get("effects"),
                fallback_unsafe=unsafe,
                fallback_kind="code.execution",
                fallback_metadata={"node_key": invocation.node.node_key},
            ),
            unsafe_side_effects=unsafe,
        )


class AiPromptNodeAdapter:
    """Execute one AI node through the existing session orchestration surface."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        prompt = str(invocation.config.get("prompt") or "").strip()
        if not prompt:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="ai node requires config.prompt",
            )
        runner = build_automation_session_runner(
            session_factory=invocation.context.session_factory,
            profile_id=invocation.context.profile_id,
            settings=invocation.context.settings,
            runner_factory=invocation.context.session_runner_factory,
        )
        session_id = (
            invocation.context.parent_session_id
            or f"automation-graph-{invocation.context.automation_id}-{invocation.context.run_id}"
        )
        node_session_id = f"{session_id}:graph:{invocation.node.node_key}"
        result = await runner.run_turn(
            profile_id=invocation.context.profile_id,
            session_id=node_session_id,
            message=_compose_ai_node_message(prompt=prompt, inputs=invocation.inputs),
            context_overrides=invocation.context.context_overrides,
            source="automation",
        )
        return NodeAdapterResult.success(
            ports={"default": _normalize_result_payload(result)},
            effects=(
                GraphNodeEffect(
                    effect_kind="ai.turn",
                    safety_class="unsafe",
                    committed=True,
                    idempotency_key=node_session_id,
                    metadata={"session_id": node_session_id},
                ),
            ),
            unsafe_side_effects=True,
        )


class AgentNodeAdapter:
    """Execute one subagent node through SubagentService."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        prompt = str(invocation.config.get("prompt") or "").strip()
        if not prompt:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="agent node requires config.prompt",
            )
        timeout_sec = _optional_int(invocation.config.get("timeout_sec"))
        if timeout_sec is None or timeout_sec <= 0:
            timeout_sec = 300
        subagent_name = str(invocation.config.get("subagent_name") or "").strip() or None
        owner_session_id = (
            invocation.context.parent_session_id
            or f"automation-graph-{invocation.context.automation_id}-{invocation.context.run_id}"
        )
        service = _build_subagent_service(invocation.context)
        try:
            accepted = await service.run(
                ctx=ToolContext(
                    profile_id=invocation.context.profile_id,
                    session_id=owner_session_id,
                    run_id=invocation.context.run_id,
                    actor="main",
                    runtime_metadata={
                        "automation_id": invocation.context.automation_id,
                        "graph_run_id": invocation.context.run_id,
                        "node_key": invocation.node.node_key,
                    },
                ),
                prompt=_compose_ai_node_message(prompt=prompt, inputs=invocation.inputs),
                subagent_name=subagent_name,
                timeout_sec=timeout_sec,
            )
            wait_response = await service.wait(
                task_id=accepted.task_id,
                timeout_sec=timeout_sec,
                profile_id=invocation.context.profile_id,
                session_id=owner_session_id,
            )
            if not wait_response.done:
                cancelled = await service.cancel(
                    task_id=accepted.task_id,
                    profile_id=invocation.context.profile_id,
                    session_id=owner_session_id,
                )
                return NodeAdapterResult.failure(
                    error_code="subagent_timeout",
                    reason="Subagent did not finish before timeout",
                    metadata={
                        "child_task_id": accepted.task_id,
                        "child_session_id": cancelled.child_session_id or wait_response.child_session_id,
                        "child_run_id": cancelled.child_run_id or wait_response.child_run_id,
                    },
                    effects=(
                        GraphNodeEffect(
                            effect_kind="subagent.run",
                            safety_class="unsafe",
                            committed=True,
                            metadata={"status": "cancelled"},
                        ),
                    ),
                    unsafe_side_effects=True,
                )
            result = await service.result(
                task_id=accepted.task_id,
                profile_id=invocation.context.profile_id,
                session_id=owner_session_id,
            )
        finally:
            await service.shutdown()
        if result.status != "completed":
            return NodeAdapterResult.failure(
                error_code=result.error_code or "subagent_failed",
                reason=result.reason or f"Subagent finished with status={result.status}",
                metadata={
                    "child_task_id": result.task_id,
                    "child_session_id": result.child_session_id,
                    "child_run_id": result.child_run_id,
                },
                effects=(
                    GraphNodeEffect(
                        effect_kind="subagent.run",
                        safety_class="unsafe",
                        committed=True,
                        metadata={"status": result.status},
                    ),
                ),
                unsafe_side_effects=True,
            )
        return NodeAdapterResult.success(
            ports={
                "default": {
                    "output": result.output,
                    "child_session_id": result.child_session_id,
                    "child_run_id": result.child_run_id,
                    "task_id": result.task_id,
                }
            },
            metadata={
                "child_task_id": result.task_id,
                "child_session_id": result.child_session_id,
                "child_run_id": result.child_run_id,
            },
            effects=(
                GraphNodeEffect(
                    effect_kind="subagent.run",
                    safety_class="unsafe",
                    committed=True,
                    metadata={"status": result.status},
                ),
            ),
            unsafe_side_effects=True,
        )


class TaskCreateNodeAdapter:
    """Create one Task Flow task through the canonical task-flow service seam."""

    def validate_spec(self, spec: object) -> tuple[str, ...]:
        if not isinstance(spec, dict):
            return ()
        node_key = str(spec.get("key") or "").strip() or "<unknown>"
        config = spec.get("config")
        if not isinstance(config, Mapping):
            return (f"task.create node `{node_key}` config must be one object",)
        errors: list[str] = []
        if not _has_config_value(config, field="title"):
            errors.append(f"task.create node `{node_key}` requires config.title or config.title_path")
        if not _has_config_value(config, field="description"):
            errors.append(
                f"task.create node `{node_key}` requires config.description or config.description_path"
            )
        if "priority" in config:
            try:
                priority = int(config["priority"])
            except (TypeError, ValueError):
                errors.append(f"task.create node `{node_key}` priority must be an integer")
            else:
                if priority < 0:
                    errors.append(f"task.create node `{node_key}` priority must be >= 0")
        for forbidden_field in (
            "profile_id",
            "profile_id_path",
            "session_id",
            "session_id_path",
            "session_profile_id",
            "session_profile_id_path",
        ):
            if str(config.get(forbidden_field) or "").strip():
                errors.append(
                    f"task.create node `{node_key}` does not support config.{forbidden_field}"
                )
        return tuple(errors)

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        title = _config_required_text(invocation, field="title")
        description = _config_required_text(invocation, field="description")
        if title is None or description is None:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="task.create requires config.title/title_path and config.description/description_path",
            )
        if any(
            str(invocation.config.get(field) or "").strip()
            for field in (
                "profile_id",
                "profile_id_path",
                "session_id",
                "session_id_path",
                "session_profile_id",
                "session_profile_id_path",
            )
        ):
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason=(
                    "task.create does not support custom profile_id/session bindings; "
                    "it always uses the automation profile and automation principal"
                ),
            )
        priority = _config_int(invocation, field="priority", default=50)
        if priority < 0:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="task.create config.priority must be >= 0",
            )
        labels = _config_string_tuple(invocation, field="labels")
        depends_on_task_ids = _config_string_tuple(invocation, field="depends_on_task_ids")
        requires_review = _config_bool(invocation, field="requires_review", default=False)
        due_at = _config_optional_datetime(invocation, field="due_at")
        if due_at is _INVALID_DATETIME:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="task.create config.due_at must be one ISO-8601 datetime string",
            )
        raw_params: dict[str, object] = {
            "title": title,
            "description": description,
            "priority": priority,
            "labels": list(labels),
            "requires_review": requires_review,
            "depends_on_task_ids": list(depends_on_task_ids),
            "source_type": _config_direct_optional_text(invocation, field="source_type")
            or "automation_graph",
            "source_ref": _config_direct_optional_text(invocation, field="source_ref")
            or f"{invocation.context.automation_id}:{invocation.context.run_id}:{invocation.node.node_key}",
        }
        for config_field in (
            "status",
            "flow_id",
            "owner_type",
            "owner_ref",
            "reviewer_type",
            "reviewer_ref",
        ):
            value = (
                _config_direct_optional_text(invocation, field=config_field)
                if config_field in {"flow_id"}
                else _config_optional_text(invocation, field=config_field)
            )
            if value is not None:
                raw_params[config_field] = value
        if due_at is not None:
            raw_params["due_at"] = due_at
        try:
            tool = _build_task_create_tool(invocation.context.settings)
            params = tool.parse_params(
                raw_params,
                default_timeout_sec=invocation.context.settings.tool_timeout_default_sec,
                max_timeout_sec=invocation.context.settings.tool_timeout_max_sec,
            )
            result = await tool.execute(
                _build_graph_tool_context(
                    invocation=invocation,
                    session_id=_default_runtime_session_id(
                        prefix="automation-graph-task",
                        invocation=invocation,
                    ),
                ),
                params,
            )
        except ToolParametersValidationError as exc:
            return NodeAdapterResult.failure(error_code=exc.error_code, reason=exc.reason)
        except (ValidationError, ValueError) as exc:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason=_sanitize_reason_text(str(exc) or type(exc).__name__),
            )
        if not result.ok:
            return NodeAdapterResult.failure(
                error_code=result.error_code or "task_create_failed",
                reason=result.reason or "task.create failed",
                metadata=result.metadata,
            )
        task_payload = result.payload.get("task")
        if not isinstance(task_payload, Mapping):
            return NodeAdapterResult.failure(
                error_code="task_create_failed",
                reason="task.create returned one invalid task payload",
            )
        payload = {"task": {str(key): value for key, value in task_payload.items()}}
        task_id = str(task_payload.get("id") or "").strip()
        task_status = str(task_payload.get("status") or "").strip()
        return NodeAdapterResult.success(
            ports={"default": payload},
            metadata={"task_id": task_id or None},
            effects=(
                GraphNodeEffect(
                    effect_kind="task.create",
                    safety_class="unsafe",
                    committed=True,
                    metadata={"task_id": task_id or None, "status": task_status or None},
                ),
            ),
            unsafe_side_effects=True,
        )


_AUTOMATION_GRAPH_ALLOWED_TOOL_NAMES = frozenset(
    {
        "task.board",
        "task.block",
        "task.comment.add",
        "task.comment.list",
        "task.create",
        "task.delegate",
        "task.dependency.add",
        "task.dependency.list",
        "task.dependency.remove",
        "task.event.list",
        "task.flow.create",
        "task.flow.get",
        "task.flow.list",
        "task.get",
        "task.inbox",
        "task.list",
        "task.review.approve",
        "task.review.list",
        "task.review.request_changes",
        "task.run.get",
        "task.run.list",
        "task.stale.list",
        "task.update",
    }
)
_AUTOMATION_GRAPH_SAFE_TOOL_NAMES = frozenset(
    {
        "task.board",
        "task.comment.list",
        "task.dependency.list",
        "task.event.list",
        "task.flow.get",
        "task.flow.list",
        "task.get",
        "task.inbox",
        "task.list",
        "task.review.list",
        "task.run.get",
        "task.run.list",
        "task.stale.list",
    }
)


class ToolRunNodeAdapter:
    """Execute one generic tool call through the shared tool registry seam."""

    def validate_spec(self, spec: object) -> tuple[str, ...]:
        if not isinstance(spec, dict):
            return ()
        node_key = str(spec.get("key") or "").strip() or "<unknown>"
        config = spec.get("config")
        if not isinstance(config, Mapping):
            return (f"action tool.run node `{node_key}` config must be one object",)
        errors: list[str] = []
        tool_name = str(config.get("tool_name") or "").strip()
        if not tool_name:
            errors.append(f"action tool.run node `{node_key}` requires config.tool_name")
        elif tool_name not in _AUTOMATION_GRAPH_ALLOWED_TOOL_NAMES:
            errors.append(
                f"action tool.run node `{node_key}` {_unsupported_graph_tool_reason(tool_name)}"
            )
        params = config.get("params")
        if params is not None and not isinstance(params, Mapping):
            errors.append(f"action tool.run node `{node_key}` config.params must be one object")
        return tuple(errors)

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        tool_name = _config_direct_required_text(invocation, field="tool_name")
        if tool_name is None:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="action.tool.run requires config.tool_name",
            )
        if tool_name not in _AUTOMATION_GRAPH_ALLOWED_TOOL_NAMES:
            return NodeAdapterResult.failure(
                error_code="tool_not_supported_in_automation_graph",
                reason=_unsupported_graph_tool_reason(tool_name),
            )
        raw_template = invocation.config.get("params")
        if raw_template is None:
            raw_params: dict[str, object] = {}
        elif isinstance(raw_template, Mapping):
            raw_params = _resolve_tool_template_mapping(raw_template, invocation.inputs)
        else:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="action.tool.run config.params must be one object",
            )
        session_id = (
            invocation.context.parent_session_id
            or _default_runtime_session_id(prefix="automation-graph-tool", invocation=invocation)
        )
        ctx = _build_graph_tool_context(invocation=invocation, session_id=session_id)
        tool_call = ToolCall(name=tool_name, params=raw_params)
        guarded = SecurityGuard().guard_tool_call(call=tool_call)
        if not guarded.allow:
            return NodeAdapterResult.failure(
                error_code=guarded.error_code or "tool_blocked_in_automation_graph",
                reason=guarded.blocked_reason or f"Tool `{tool_name}` is blocked in automation graph runtime",
            )
        sensitive_block = _blocked_sensitive_tool_result(
            tool_name=tool_name,
            runtime_metadata=ctx.runtime_metadata,
        )
        if sensitive_block is not None:
            return _tool_result_to_failure(sensitive_block)
        channel_block = _blocked_channel_tool_result(
            tool_name=tool_name,
            runtime_metadata=ctx.runtime_metadata,
        )
        if channel_block is not None:
            return _tool_result_to_failure(channel_block)
        runtime = _build_graph_tool_execution_runtime(
            settings=invocation.context.settings,
            profile_id=invocation.context.profile_id,
        )
        policy = await _load_graph_tool_policy(
            session_factory=invocation.context.session_factory,
            profile_id=invocation.context.profile_id,
        )
        try:
            results = await runtime.execute_requested_tool_calls(
                run_id=invocation.context.run_id,
                session_id=session_id,
                profile_id=invocation.context.profile_id,
                tool_calls=[guarded.execution_call],
                policy=policy,
                automation_intent=True,
                explicit_skill_requests=set(),
                explicit_subagent_requests=set(),
                allow_confirmation_markers=False,
                runtime_metadata=ctx.runtime_metadata,
                allowed_tool_names=set(_AUTOMATION_GRAPH_ALLOWED_TOOL_NAMES),
            )
        except Exception as exc:
            return NodeAdapterResult.failure(
                error_code="tool_execution_failed",
                reason=_sanitize_reason_text(f"{type(exc).__name__}: {exc}"),
                effects=(
                    GraphNodeEffect(
                        effect_kind="tool.run",
                        safety_class=_graph_tool_safety_class(tool_name),
                        committed=False,
                        metadata={"tool_name": tool_name},
                    ),
                ),
                unsafe_side_effects=tool_name not in _AUTOMATION_GRAPH_SAFE_TOOL_NAMES,
            )
        result = results[0] if results else ToolResult.error(
            error_code="tool_execution_failed",
            reason=f"Tool did not return one result: {tool_name}",
        )
        safety_class = _graph_tool_safety_class(tool_name)
        effect = GraphNodeEffect(
            effect_kind="tool.run",
            safety_class=safety_class,
            committed=bool(result.ok),
            metadata={"tool_name": tool_name},
        )
        if not result.ok:
            return NodeAdapterResult.failure(
                error_code=result.error_code or "tool_execution_failed",
                reason=result.reason or f"Tool failed: {tool_name}",
                metadata=result.metadata,
                effects=(effect,),
                unsafe_side_effects=safety_class == "unsafe",
            )
        return NodeAdapterResult.success(
            ports={"default": dict(result.payload)},
            metadata=result.metadata,
            effects=(effect,),
            unsafe_side_effects=safety_class == "unsafe",
        )


class AppRunNodeAdapter:
    """Execute one integration app action through the unified app runtime seam."""

    def validate_spec(self, spec: object) -> tuple[str, ...]:
        if not isinstance(spec, dict):
            return ()
        node_key = str(spec.get("key") or "").strip() or "<unknown>"
        config = spec.get("config")
        if not isinstance(config, Mapping):
            return (f"action app.run node `{node_key}` config must be one object",)
        errors: list[str] = []
        if not str(config.get("app_name") or "").strip():
            errors.append(f"action app.run node `{node_key}` requires config.app_name")
        if not str(config.get("action") or "").strip():
            errors.append(f"action app.run node `{node_key}` requires config.action")
        if str(config.get("profile_name_path") or "").strip():
            errors.append(
                f"action app.run node `{node_key}` does not support config.profile_name_path"
            )
        params = config.get("params")
        if params is not None and not isinstance(params, Mapping):
            errors.append(f"action app.run node `{node_key}` config.params must be one object")
        if "timeout_sec" in config:
            try:
                timeout_sec = int(config["timeout_sec"])
            except (TypeError, ValueError):
                errors.append(f"action app.run node `{node_key}` timeout_sec must be an integer")
            else:
                if timeout_sec <= 0:
                    errors.append(f"action app.run node `{node_key}` timeout_sec must be > 0")
        return tuple(errors)

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult:
        app_name = _config_direct_required_text(invocation, field="app_name")
        action = _config_direct_required_text(invocation, field="action")
        if app_name is None or action is None:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason="action.app.run requires config.app_name and config.action",
            )
        params = _config_mapping(invocation, field="params")
        dynamic_params = _config_mapping(invocation, field="params", allow_path_only=True)
        merged_params = dict(params)
        merged_params.update(dynamic_params)
        session_id = (
            invocation.context.parent_session_id
            or _default_runtime_session_id(prefix="automation-graph-action", invocation=invocation)
        )
        raw_params: dict[str, object] = {
            "app_name": app_name,
            "action": action,
            "params": merged_params,
        }
        profile_name = _config_direct_optional_text(invocation, field="profile_name")
        if profile_name is not None:
            raw_params["profile_name"] = profile_name
        if "timeout_sec" in invocation.config:
            raw_params["timeout_sec"] = invocation.config["timeout_sec"]
        try:
            tool = _build_app_run_tool(invocation.context.settings)
            params_model = tool.parse_params(
                raw_params,
                default_timeout_sec=invocation.context.settings.tool_timeout_default_sec,
                max_timeout_sec=invocation.context.settings.tool_timeout_max_sec,
            )
            result = await tool.execute(
                _build_graph_tool_context(invocation=invocation, session_id=session_id),
                params_model,
            )
        except ToolParametersValidationError as exc:
            return NodeAdapterResult.failure(error_code=exc.error_code, reason=exc.reason)
        except (ValidationError, ValueError) as exc:
            return NodeAdapterResult.failure(
                error_code="graph_node_invalid",
                reason=_sanitize_reason_text(str(exc) or type(exc).__name__),
            )
        effect = GraphNodeEffect(
            effect_kind="app.run",
            safety_class="unsafe",
            committed=bool(result.ok),
            metadata={"app_name": app_name, "action": action},
        )
        if not result.ok:
            return NodeAdapterResult.failure(
                error_code=result.error_code or "app_action_failed",
                reason=result.reason or f"App action failed: {app_name}.{action}",
                metadata=result.metadata,
                effects=(effect,),
                unsafe_side_effects=True,
            )
        return NodeAdapterResult.success(
            ports={"default": dict(result.payload)},
            metadata=result.metadata,
            effects=(effect,),
            unsafe_side_effects=True,
        )


def build_default_node_adapter_registry() -> AutomationGraphNodeAdapterRegistry:
    """Build the canonical adapter registry for runtime and validation surfaces."""

    registry = AutomationGraphNodeAdapterRegistry()
    registry.register(node_kind="builtin", node_type="trigger.input", adapter=TriggerInputNodeAdapter())
    registry.register(node_kind="builtin", node_type="passthrough", adapter=PassthroughNodeAdapter())
    registry.register(node_kind="builtin", node_type="switch.value", adapter=SwitchValueNodeAdapter())
    registry.register(node_kind="builtin", node_type="error.raise", adapter=ErrorRaiseNodeAdapter())
    registry.register(node_kind="code", node_type="python", adapter=CodePythonNodeAdapter())
    registry.register(node_kind="ai", node_type="prompt", adapter=AiPromptNodeAdapter())
    registry.register(node_kind="agent", node_type="subagent.run", adapter=AgentNodeAdapter())
    registry.register(node_kind="task", node_type="task.create", adapter=TaskCreateNodeAdapter())
    registry.register(node_kind="action", node_type="tool.run", adapter=ToolRunNodeAdapter())
    registry.register(node_kind="action", node_type="app.run", adapter=AppRunNodeAdapter())
    return registry


class AutomationGraphExecutor:
    """Topological graph executor with terminal-first trace semantics."""

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        with_repo: Callable[
            [Callable[[AutomationGraphRepository], Awaitable[object]]],
            Awaitable[object],
        ],
        registry: AutomationGraphNodeAdapterRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._with_repo = with_repo
        self._registry = registry or build_default_node_adapter_registry()

    async def execute(
        self,
        *,
        graph: LoadedGraph,
        context: GraphExecutionContext,
    ) -> GraphExecutionOutcome:
        nodes_by_id = {node.id: node for node in graph.nodes}
        outgoing_by_id: dict[int, list[AutomationEdge]] = {node.id: [] for node in graph.nodes}
        incoming_by_id: dict[int, list[AutomationEdge]] = {node.id: [] for node in graph.nodes}
        for edge in graph.edges:
            outgoing_by_id[edge.source_node_id].append(edge)
            incoming_by_id[edge.target_node_id].append(edge)
        states = {
            node.id: PendingNodeState(incoming_total=len(incoming_by_id[node.id]))
            for node in graph.nodes
        }
        outputs_by_node_id: dict[int, dict[str, object]] = {}
        unsafe_side_effects = False
        execution_counter = [0]
        ready: deque[AutomationNode] = deque()
        for node in graph.nodes:
            if states[node.id].incoming_total == 0:
                ready.append(node)
                states[node.id].ready_enqueued = True
        if not ready:
            return GraphExecutionOutcome(
                status="failed",
                final_output=None,
                unsafe_side_effects=False,
                error_code="automation_graph_invalid",
                reason="Graph has no entry nodes",
            )
        while ready:
            node = ready.popleft()
            state = states[node.id]
            state.ready_enqueued = False
            if state.finalized:
                continue
            normalized_input = _normalize_state_inputs(state.inputs)
            node_schemas = graph.schemas_by_node_id.get(node.id)
            input_error = _validate_schema_payload(
                schema=node_schemas.input_schema if node_schemas is not None else None,
                payload=normalized_input,
                error_code="graph_node_input_invalid",
            )
            execution_index = _advance_execution_index(execution_counter)
            await self._mark_node_running(
                run_id=context.run_id,
                node_id=node.id,
                execution_index=execution_index,
                input_payload=normalized_input,
            )
            if input_error is not None:
                await self._mark_node_failed(
                    run_id=context.run_id,
                    node_id=node.id,
                    reason=input_error[1],
                    error_code=input_error[0],
                )
                return GraphExecutionOutcome(
                    status="failed",
                    final_output=None,
                    unsafe_side_effects=unsafe_side_effects,
                    error_code="automation_graph_failed",
                    reason=input_error[1],
                )
            adapter = self._registry.get(node_kind=node.node_kind, node_type=node.node_type)
            if adapter is None:
                await self._mark_node_failed(
                    run_id=context.run_id,
                    node_id=node.id,
                    reason=f"Unsupported node adapter: {node.node_kind}/{node.node_type}",
                    error_code="graph_node_unsupported",
                )
                return GraphExecutionOutcome(
                    status="failed",
                    final_output=None,
                    unsafe_side_effects=unsafe_side_effects,
                    error_code="automation_graph_failed",
                    reason=f"Unsupported node adapter: {node.node_kind}/{node.node_type}",
                )
            try:
                result = await adapter.execute(
                    NodeInvocation(
                        node=node,
                        config=_parse_json_dict(node.config_json),
                        inputs=normalized_input,
                        version=graph.versions_by_id.get(node.node_version_id or -1),
                        context=context,
                    )
                )
            except Exception as exc:
                node_unsafe = node.node_kind in {"ai", "action", "task"}
                await self._mark_node_failed(
                    run_id=context.run_id,
                    node_id=node.id,
                    reason=_format_unhandled_node_exception(exc),
                    error_code="graph_node_exception",
                )
                return GraphExecutionOutcome(
                    status="failed",
                    final_output=None,
                    unsafe_side_effects=unsafe_side_effects or node_unsafe,
                    error_code="automation_graph_failed",
                    reason=_format_unhandled_node_exception(exc),
                )
            if not result.ok:
                await self._mark_node_failed(
                    run_id=context.run_id,
                    node_id=node.id,
                    reason=result.reason or "graph node failed",
                    error_code=result.error_code or "graph_node_failed",
                    metadata=result.metadata,
                    effects=result.effects,
                )
                return GraphExecutionOutcome(
                    status="failed",
                    final_output=None,
                    unsafe_side_effects=unsafe_side_effects or result.unsafe_side_effects,
                    error_code="automation_graph_failed",
                    reason=_sanitize_reason_text(result.reason or "graph node failed"),
                )
            unsafe_side_effects = unsafe_side_effects or result.unsafe_side_effects
            selected_ports = tuple(str(item) for item in result.selected_ports)
            ports = {str(key): value for key, value in result.ports.items()}
            output_error = _validate_schema_payload(
                schema=node_schemas.output_schema if node_schemas is not None else None,
                payload=ports,
                error_code="graph_node_output_invalid",
            )
            if output_error is not None:
                await self._mark_node_failed(
                    run_id=context.run_id,
                    node_id=node.id,
                    reason=output_error[1],
                    error_code=output_error[0],
                    metadata=result.metadata,
                    effects=result.effects,
                )
                return GraphExecutionOutcome(
                    status="failed",
                    final_output=None,
                    unsafe_side_effects=unsafe_side_effects,
                    error_code="automation_graph_failed",
                    reason=output_error[1],
                )
            outputs_by_node_id[node.id] = ports
            await self._mark_node_succeeded(
                run_id=context.run_id,
                node_id=node.id,
                output_payload=ports,
                selected_ports=selected_ports,
                metadata=result.metadata,
                effects=result.effects,
            )
            state.finalized = True
            for edge in outgoing_by_id[node.id]:
                delivered = edge.source_port in ports
                await self._resolve_edge(
                    edge=edge,
                    delivered=delivered,
                    payload=ports.get(edge.source_port),
                    states=states,
                    nodes_by_id=nodes_by_id,
                    outgoing_by_id=outgoing_by_id,
                    ready=ready,
                    run_id=context.run_id,
                    execution_counter=execution_counter,
                )
        final_output = _compute_final_output(
            nodes=graph.nodes,
            outgoing_by_id=outgoing_by_id,
            outputs_by_node_id=outputs_by_node_id,
        )
        return GraphExecutionOutcome(
            status="succeeded",
            final_output=final_output,
            unsafe_side_effects=unsafe_side_effects,
        )

    async def _resolve_edge(
        self,
        *,
        edge: AutomationEdge,
        delivered: bool,
        payload: object,
        states: Mapping[int, PendingNodeState],
        nodes_by_id: Mapping[int, AutomationNode],
        outgoing_by_id: Mapping[int, list[AutomationEdge]],
        ready: deque[AutomationNode],
        run_id: int,
        execution_counter: list[int],
    ) -> None:
        target_state = states[edge.target_node_id]
        target_state.resolved_incoming += 1
        if delivered:
            target_state.inputs.setdefault(edge.target_port, []).append(payload)
            target_state.delivered_incoming += 1
        if target_state.resolved_incoming != target_state.incoming_total:
            return
        if target_state.delivered_incoming > 0:
            if not target_state.ready_enqueued and not target_state.finalized:
                ready.append(nodes_by_id[edge.target_node_id])
                target_state.ready_enqueued = True
            return
        if target_state.finalized:
            return
        target_state.finalized = True
        await self._mark_node_skipped(
            run_id=run_id,
            node_id=edge.target_node_id,
            execution_index=_advance_execution_index(execution_counter),
        )
        for outgoing in outgoing_by_id[edge.target_node_id]:
            await self._resolve_edge(
                edge=outgoing,
                delivered=False,
                payload=None,
                states=states,
                nodes_by_id=nodes_by_id,
                outgoing_by_id=outgoing_by_id,
                ready=ready,
                run_id=run_id,
                execution_counter=execution_counter,
            )

    async def _mark_node_running(
        self,
        *,
        run_id: int,
        node_id: int,
        execution_index: int,
        input_payload: Mapping[str, object],
    ) -> None:
        async def _op(repo: AutomationGraphRepository) -> None:
            await repo.update_node_run(
                run_id=run_id,
                node_id=node_id,
                status="running",
                execution_index=execution_index,
                input_json=_json_dumps(_sanitize_mapping(input_payload)),
                started_at=datetime.now(timezone.utc),
            )

        await self._with_repo(_op)

    async def _mark_node_succeeded(
        self,
        *,
        run_id: int,
        node_id: int,
        output_payload: Mapping[str, object],
        selected_ports: tuple[str, ...],
        metadata: Mapping[str, object],
        effects: tuple[GraphNodeEffect, ...],
    ) -> None:
        async def _op(repo: AutomationGraphRepository) -> None:
            await repo.update_node_run(
                run_id=run_id,
                node_id=node_id,
                status="succeeded",
                effects_json=_json_dumps(_effects_to_json_payload(effects)) if effects else None,
                output_json=_json_dumps(_sanitize_mapping(output_payload)),
                selected_ports_json=_json_dumps(list(selected_ports)),
                child_task_id=_optional_text(metadata.get("child_task_id")),
                child_session_id=_optional_text(metadata.get("child_session_id")),
                child_run_id=_optional_int(metadata.get("child_run_id")),
                completed_at=datetime.now(timezone.utc),
            )

        await self._with_repo(_op)

    async def _mark_node_failed(
        self,
        *,
        run_id: int,
        node_id: int,
        reason: str,
        error_code: str,
        metadata: Mapping[str, object] | None = None,
        effects: tuple[GraphNodeEffect, ...] = (),
    ) -> None:
        data = metadata or {}
        safe_reason = _sanitize_reason_text(reason)
        async def _op(repo: AutomationGraphRepository) -> None:
            await repo.update_node_run(
                run_id=run_id,
                node_id=node_id,
                status="failed",
                effects_json=_json_dumps(_effects_to_json_payload(effects)) if effects else None,
                error_code=error_code,
                reason=safe_reason,
                child_task_id=_optional_text(data.get("child_task_id")),
                child_session_id=_optional_text(data.get("child_session_id")),
                child_run_id=_optional_int(data.get("child_run_id")),
                completed_at=datetime.now(timezone.utc),
            )

        await self._with_repo(_op)

    async def _mark_node_skipped(self, *, run_id: int, node_id: int, execution_index: int) -> None:
        async def _op(repo: AutomationGraphRepository) -> None:
            await repo.update_node_run(
                run_id=run_id,
                node_id=node_id,
                status="skipped",
                execution_index=execution_index,
                reason="No inbound branch delivered payload",
                completed_at=datetime.now(timezone.utc),
            )

        await self._with_repo(_op)

def _normalize_state_inputs(inputs: Mapping[str, list[object]]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, values in inputs.items():
        if len(values) == 1:
            normalized[key] = values[0]
        else:
            normalized[key] = list(values)
    return normalized


def _compute_final_output(
    *,
    nodes: tuple[AutomationNode, ...],
    outgoing_by_id: Mapping[int, list[AutomationEdge]],
    outputs_by_node_id: Mapping[int, dict[str, object]],
) -> dict[str, object] | None:
    leaf_nodes = [node for node in nodes if not outgoing_by_id[node.id] and node.id in outputs_by_node_id]
    if not leaf_nodes:
        return None
    if len(leaf_nodes) == 1:
        return outputs_by_node_id[leaf_nodes[0].id]
    return {node.node_key: outputs_by_node_id[node.id] for node in leaf_nodes}


_INVALID_DATETIME = object()


def _config_value(
    invocation: NodeInvocation,
    *,
    field: str,
    allow_path_only: bool = False,
) -> object | None:
    if allow_path_only:
        path = str(invocation.config.get(f"{field}_path") or "").strip()
        if not path:
            return None
        return _resolve_path(invocation.inputs, path)
    direct_value = invocation.config.get(field)
    if direct_value is not None:
        return direct_value
    path = str(invocation.config.get(f"{field}_path") or "").strip()
    if not path:
        return None
    return _resolve_path(invocation.inputs, path)


def _has_config_value(config: Mapping[str, object], *, field: str) -> bool:
    if str(config.get(field) or "").strip():
        return True
    return bool(str(config.get(f"{field}_path") or "").strip())


def _config_required_text(invocation: NodeInvocation, *, field: str) -> str | None:
    value = _config_value(invocation, field=field)
    text = str(value or "").strip()
    return text or None


def _config_direct_required_text(invocation: NodeInvocation, *, field: str) -> str | None:
    text = _config_direct_optional_text(invocation, field=field)
    return text or None


def _config_optional_text(invocation: NodeInvocation, *, field: str) -> str | None:
    value = _config_value(invocation, field=field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _config_direct_optional_text(invocation: NodeInvocation, *, field: str) -> str | None:
    value = invocation.config.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _config_int(invocation: NodeInvocation, *, field: str, default: int) -> int:
    value = _config_value(invocation, field=field)
    if value is None or value == "":
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _config_bool(invocation: NodeInvocation, *, field: str, default: bool) -> bool:
    value = _config_value(invocation, field=field)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _config_string_tuple(invocation: NodeInvocation, *, field: str) -> tuple[str, ...]:
    value = _config_value(invocation, field=field)
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _config_mapping(
    invocation: NodeInvocation,
    *,
    field: str,
    allow_path_only: bool = False,
) -> dict[str, object]:
    value = _config_value(invocation, field=field, allow_path_only=allow_path_only)
    if not isinstance(value, Mapping):
        return {}
    return {str(key): inner for key, inner in value.items()}


def _config_optional_datetime(
    invocation: NodeInvocation,
    *,
    field: str,
) -> datetime | None | object:
    value = _config_value(invocation, field=field)
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return _INVALID_DATETIME
    return _INVALID_DATETIME


def _default_runtime_session_id(*, prefix: str, invocation: NodeInvocation) -> str:
    return (
        f"{prefix}-{invocation.context.automation_id}-{invocation.context.run_id}-{invocation.node.node_key}"
    )


def _tool_result_to_failure(
    result: ToolResult,
    *,
    unsafe_side_effects: bool = False,
) -> NodeAdapterResult:
    return NodeAdapterResult.failure(
        error_code=result.error_code or "tool_execution_failed",
        reason=result.reason or "Tool execution failed",
        metadata=result.metadata,
        unsafe_side_effects=unsafe_side_effects,
    )


def _unsupported_graph_tool_reason(tool_name: str) -> str:
    normalized_name = tool_name.strip()
    if normalized_name == "app.run":
        return "does not allow tool `app.run`; use dedicated node_type `app.run` instead"
    return (
        f"supports only curated automation data-plane tools; "
        f"tool `{normalized_name}` is not allowed in automation graph runtime"
    )


def _graph_tool_safety_class(tool_name: str) -> Literal["safe", "unsafe"]:
    return "safe" if tool_name in _AUTOMATION_GRAPH_SAFE_TOOL_NAMES else "unsafe"


def _tool_requires_automation_intent(*, registry: _ToolLookup, tool_name: str) -> bool:
    tool = registry.get(tool_name)
    if tool is not None:
        return bool(getattr(tool, "requires_automation_intent", False))
    return tool_name.startswith("automation.")


def _build_graph_tool_execution_runtime(
    *,
    settings: Settings,
    profile_id: str,
) -> ToolExecutionRuntime:
    from afkbot.services.tools.registry import ToolRegistry

    registry = ToolRegistry.from_profile_settings(settings, profile_id=profile_id)
    return build_guarded_tool_execution_runtime(
        settings=settings,
        tool_registry=registry,
        policy_engine=PolicyEngine(root_dir=settings.root_dir),
        tool_timeout_default_sec=settings.tool_timeout_default_sec,
        tool_timeout_max_sec=settings.tool_timeout_max_sec,
        parallel_tool_max_concurrent=1,
        log_event=_noop_log_event,
        raise_if_cancel_requested=_noop_cancel_check,
        log_skill_read=_noop_skill_read,
        sanitize=_loop_sanitize,
        sanitize_value=_loop_sanitize_value,
        to_params_dict=_loop_to_params_dict,
        tool_log_payload=lambda *, tool_name, payload: _loop_tool_log_payload(
            tool_name=tool_name,
            payload=payload,
            redact_fields=frozenset(),
        ),
        tool_requires_automation_intent=lambda *, tool_name: _tool_requires_automation_intent(
            registry=registry,
            tool_name=tool_name,
        ),
    )


async def _load_graph_tool_policy(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
) -> ProfilePolicy:
    async with session_factory() as session:
        repo = ProfilePolicyRepository(session)
        row = await repo.get(profile_id)
        if row is not None:
            return row
    from afkbot.models.profile_policy import ProfilePolicy

    return ProfilePolicy(
        profile_id=profile_id,
        policy_enabled=True,
        policy_preset="medium",
        policy_capabilities_json="[]",
        max_iterations_main=8,
        max_iterations_subagent=8,
        allowed_tools_json="[]",
        denied_tools_json="[]",
        allowed_directories_json="[]",
        shell_allowed_commands_json="[]",
        shell_denied_commands_json="[]",
        network_allowlist_json="[]",
    )


def _resolve_tool_template_mapping(
    template: Mapping[str, object],
    inputs: Mapping[str, object],
) -> dict[str, object]:
    return {
        str(key): _resolve_tool_template_value(value=item, inputs=inputs)
        for key, item in template.items()
    }


def _resolve_tool_template_value(*, value: object, inputs: Mapping[str, object]) -> object:
    if isinstance(value, Mapping):
        if set(value.keys()) == {"$path"}:
            return _resolve_path(inputs, str(value["$path"] or "").strip())
        return {
            str(key): _resolve_tool_template_value(value=item, inputs=inputs)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_tool_template_value(value=item, inputs=inputs) for item in value]
    return value


def _resolve_path(payload: object, path: str) -> object | None:
    if not path:
        return payload
    current = payload
    for segment in path.split("."):
        key = segment.strip()
        if not key:
            continue
        if isinstance(current, Mapping) and key in current:
            current = current[key]
            continue
        return None
    return current


def _compose_ai_node_message(*, prompt: str, inputs: Mapping[str, object]) -> str:
    payload = json.dumps(_sanitize_mapping(inputs), ensure_ascii=True, sort_keys=True)
    return f"{prompt}\n\nnode_input={payload}"


def _normalize_result_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): inner for key, inner in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return list(value)
    return str(value)


def _normalize_json_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): inner for key, inner in value.items()}


def _normalize_effects_payload(
    raw: object,
    *,
    fallback_unsafe: bool,
    fallback_kind: str,
    fallback_metadata: Mapping[str, object] | None = None,
) -> tuple[GraphNodeEffect, ...]:
    effects: list[GraphNodeEffect] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            effect_kind = str(item.get("effect_kind") or "").strip()
            if not effect_kind:
                continue
            safety_class = str(item.get("safety_class") or "safe").strip().lower()
            if safety_class not in {"safe", "unsafe"}:
                safety_class = "unsafe" if fallback_unsafe else "safe"
            effects.append(
                GraphNodeEffect(
                    effect_kind=effect_kind,
                    safety_class="unsafe" if safety_class == "unsafe" else "safe",
                    committed=bool(item.get("committed", False)),
                    idempotency_key=_optional_text(item.get("idempotency_key")),
                    metadata=_sanitize_mapping(_normalize_json_mapping(item.get("metadata"))),
                )
            )
    if fallback_unsafe and not effects:
        effects.append(
            GraphNodeEffect(
                effect_kind=fallback_kind,
                safety_class="unsafe",
                committed=True,
                metadata={}
                if fallback_metadata is None
                else _sanitize_mapping(dict(fallback_metadata)),
            )
        )
    return tuple(effects)


def _parse_json_dict(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): value for key, value in parsed.items()}


def _validate_schema_payload(
    *,
    schema: Mapping[str, object] | None,
    payload: object,
    error_code: str,
) -> tuple[str, str] | None:
    if not schema:
        return None
    validator = Draft202012Validator(dict(schema))
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if not errors:
        return None
    first_error = errors[0]
    dotted_path = ".".join(str(part) for part in first_error.absolute_path)
    prefix = f" at {dotted_path}" if dotted_path else ""
    return (error_code, f"Graph node payload invalid{prefix}: {first_error.message}"[:2000])


def _has_unsafe_effects(effects: tuple[GraphNodeEffect, ...]) -> bool:
    return any(effect.committed and effect.safety_class == "unsafe" for effect in effects)


def _effects_to_json_payload(effects: tuple[GraphNodeEffect, ...]) -> list[dict[str, object]]:
    return [
        {
            "effect_kind": effect.effect_kind,
            "safety_class": effect.safety_class,
            "committed": effect.committed,
            "idempotency_key": effect.idempotency_key,
            "metadata": _sanitize_mapping(dict(effect.metadata)),
        }
        for effect in effects
    ]


def _advance_execution_index(counter: list[int]) -> int:
    counter[0] += 1
    return counter[0]


async def _noop_log_event(**_kwargs: object) -> None:
    return None


async def _noop_cancel_check(**_kwargs: object) -> None:
    return None


async def _noop_skill_read(**_kwargs: object) -> None:
    return None


class _StreamLimitExceeded(RuntimeError):
    """Raised when worker stdio exceeds the configured byte budget."""


async def _communicate_limited(
    *,
    process: asyncio.subprocess.Process,
    stdin_bytes: bytes,
    io_limit: int,
) -> tuple[bytes, bytes]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("Code node process pipes not initialized")
    process.stdin.write(stdin_bytes)
    await process.stdin.drain()
    process.stdin.close()
    stdout_task = asyncio.create_task(_read_stream_limited(process.stdout, io_limit, stream_name="stdout"))
    stderr_task = asyncio.create_task(_read_stream_limited(process.stderr, io_limit, stream_name="stderr"))
    wait_task = asyncio.create_task(process.wait())
    try:
        stdout, stderr, _ = await asyncio.gather(stdout_task, stderr_task, wait_task)
    except Exception:
        for task in (stdout_task, stderr_task, wait_task):
            task.cancel()
        raise
    return stdout, stderr


async def _read_stream_limited(
    stream: asyncio.StreamReader,
    io_limit: int,
    *,
    stream_name: str,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        total += len(chunk)
        if total > io_limit:
            raise _StreamLimitExceeded(
                f"Code node {stream_name} exceeded {io_limit} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _build_subagent_service(context: GraphExecutionContext) -> SubagentServiceProtocol:
    if context.subagent_service_factory is not None:
        return context.subagent_service_factory(context.settings)
    from afkbot.services.subagents.service import SubagentService

    return SubagentService(context.settings)


def _build_graph_tool_context(*, invocation: NodeInvocation, session_id: str) -> ToolContext:
    return ToolContext(
        profile_id=invocation.context.profile_id,
        session_id=session_id,
        run_id=invocation.context.run_id,
        runtime_metadata={
            "automation_graph": {
                "automation_id": invocation.context.automation_id,
                "run_id": invocation.context.run_id,
                "node_key": invocation.node.node_key,
                "trigger_type": invocation.context.trigger_type,
            }
        },
    )


def _build_task_create_tool(settings: Settings) -> ToolBase:
    from afkbot.services.tools.plugins.task_create.plugin import create_tool

    return create_tool(settings)


def _build_app_run_tool(settings: Settings) -> ToolBase:
    from afkbot.services.tools.plugins.app_run.plugin import create_tool

    return create_tool(settings)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _blocked_sensitive_tool_result(
    *,
    tool_name: str,
    runtime_metadata: dict[str, object] | None,
) -> ToolResult | None:
    from afkbot.services.agent_loop.sensitive_tool_policy import blocked_tool_result

    return blocked_tool_result(tool_name=tool_name, runtime_metadata=runtime_metadata)


def _blocked_channel_tool_result(
    *,
    tool_name: str,
    runtime_metadata: dict[str, object] | None,
) -> ToolResult | None:
    from afkbot.services.agent_loop.channel_tool_policy import blocked_tool_result_for_runtime

    return blocked_tool_result_for_runtime(tool_name=tool_name, runtime_metadata=runtime_metadata)


def _sanitize_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): sanitize_payload_value(inner, field_name=str(key))
        for key, inner in value.items()
    }


def _format_unhandled_node_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return _sanitize_reason_text(f"{type(exc).__name__}: {message}")
    return _sanitize_reason_text(type(exc).__name__)


def _sanitize_reason_text(value: str) -> str:
    redacted = sanitize_payload_value(value, field_name="reason")
    return str(redacted)[:2000]
