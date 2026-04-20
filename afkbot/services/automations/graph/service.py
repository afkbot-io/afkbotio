"""Service layer for automation graph definitions, execution, and terminal read models."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, TypeVar

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.models.automation_node import AutomationNode
from afkbot.models.automation_node_run import AutomationNodeRun
from afkbot.models.automation_run import AutomationRun
from afkbot.repositories.automation_graph_repo import AutomationGraphRepository
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.graph.contracts import (
    AutomationGraphEdgeMetadata,
    AutomationGraphFallbackTraceMetadata,
    AutomationGraphMetadata,
    AutomationGraphNodeEffectMetadata,
    AutomationGraphNodeMetadata,
    AutomationGraphNodeRunMetadata,
    AutomationGraphRunMetadata,
    AutomationGraphSpec,
    AutomationGraphTraceMetadata,
    AutomationGraphValidationReport,
)
from afkbot.services.automations.graph.executor import (
    AutomationGraphExecutor,
    AutomationGraphSubagentFactory,
    GraphExecutionContext,
    GraphExecutionOutcome,
    LoadedGraph,
    LoadedNodeSchemas,
    build_default_node_adapter_registry,
)
from afkbot.services.automations.graph.node_registry import AutomationGraphNodeAdapterRegistry
from afkbot.services.automations.message_factory import compose_cron_message, compose_webhook_message
from afkbot.services.automations.metadata import as_execution_mode
from afkbot.services.automations.payloads import sanitize_payload, sanitize_payload_value
from afkbot.services.automations.session_runner_factory import (
    AutomationSessionRunnerFactory,
    build_automation_session_runner,
)
from afkbot.services.profile_runtime import get_profile_runtime_config_service
from afkbot.settings import Settings

if TYPE_CHECKING:
    from afkbot.services.agent_loop.turn_context import TurnContextOverrides

TRepoValue = TypeVar("TRepoValue")


class AutomationGraphService:
    """Own graph persistence, execution, and terminal-facing read models."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        subagent_service_factory: AutomationGraphSubagentFactory | None = None,
        registry: AutomationGraphNodeAdapterRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._subagent_service_factory = subagent_service_factory
        self._registry = registry or build_default_node_adapter_registry()

    async def apply_graph(
        self,
        *,
        profile_id: str,
        automation_id: int,
        spec: AutomationGraphSpec,
    ) -> AutomationGraphMetadata:
        """Replace the active graph for one automation with a validated spec."""

        report = self.validate_spec(spec)
        if not report.valid:
            raise AutomationsServiceError(
                error_code="invalid_graph_spec",
                reason="; ".join(report.errors),
            )

        async def _op(repo: AutomationGraphRepository) -> int:
            automation = await repo.get_automation(profile_id=profile_id, automation_id=automation_id)
            if automation is None or automation.status == "deleted":
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            existing_flows = await repo.list_flows(automation_id=automation_id)
            next_version = max((flow.version for flow in existing_flows), default=0) + 1
            await repo.archive_active_flows(automation_id=automation_id)
            flow = await repo.create_flow(
                automation_id=automation_id,
                name=spec.name,
                version=next_version,
            )
            nodes_by_key: dict[str, AutomationNode] = {}
            for node_spec in spec.nodes:
                definition_id: int | None = None
                version_id: int | None = None
                if node_spec.node_kind == "code":
                    if node_spec.version is None:
                        raise AutomationsServiceError(
                            error_code="invalid_graph_spec",
                            reason=f"Code node `{node_spec.key}` requires one inline version spec",
                        )
                    slug = f"automation:{automation_id}:{node_spec.key}"
                    definition = await repo.get_definition(profile_id=profile_id, slug=slug)
                    if definition is None:
                        definition = await repo.create_definition(
                            profile_id=profile_id,
                            slug=slug,
                            node_kind=node_spec.node_kind,
                            node_type=node_spec.node_type,
                            display_name=node_spec.name,
                        )
                    definition_id = definition.id
                    version_count = await repo.count_versions(node_definition_id=definition.id)
                    version = await repo.create_version(
                        node_definition_id=definition.id,
                        version_label=node_spec.version.version_label or f"v{version_count + 1}",
                        runtime=node_spec.version.runtime,
                        config_schema_json=_json_dumps(node_spec.version.config_schema)
                        if node_spec.version.config_schema is not None
                        else None,
                        input_schema_json=_json_dumps(node_spec.version.input_schema)
                        if node_spec.version.input_schema is not None
                        else None,
                        output_schema_json=_json_dumps(node_spec.version.output_schema)
                        if node_spec.version.output_schema is not None
                        else None,
                        manifest_json=_json_dumps(node_spec.version.manifest),
                        tests_json=_json_dumps(node_spec.version.tests)
                        if node_spec.version.tests is not None
                        else None,
                        source_code=node_spec.version.source_code,
                    )
                    version_id = version.id
                node = await repo.create_node(
                    flow_id=flow.id,
                    node_key=node_spec.key,
                    name=node_spec.name,
                    node_kind=node_spec.node_kind,
                    node_type=node_spec.node_type,
                    config_json=_json_dumps(node_spec.config),
                    node_definition_id=definition_id,
                    node_version_id=version_id,
                )
                nodes_by_key[node_spec.key] = node
            for edge_spec in spec.edges:
                await repo.create_edge(
                    flow_id=flow.id,
                    source_node_id=nodes_by_key[edge_spec.source_key].id,
                    target_node_id=nodes_by_key[edge_spec.target_key].id,
                    source_port=edge_spec.source_port,
                    target_port=edge_spec.target_port,
                )
            await repo.set_automation_execution_mode(
                profile_id=profile_id,
                automation_id=automation_id,
                execution_mode="graph",
            )
            return flow.id

        await self._with_repo(_op)
        return await self.get_graph(profile_id=profile_id, automation_id=automation_id)

    async def get_graph(self, *, profile_id: str, automation_id: int) -> AutomationGraphMetadata:
        """Return the active graph snapshot for one automation."""

        graph = await self._load_graph(profile_id=profile_id, automation_id=automation_id)
        return _to_graph_metadata(graph)

    async def validate_graph(
        self,
        *,
        profile_id: str,
        automation_id: int,
    ) -> AutomationGraphValidationReport:
        """Validate the active persisted graph for one automation."""

        graph = await self._load_graph(profile_id=profile_id, automation_id=automation_id)
        return self.validate_loaded_graph(graph)

    async def list_runs(
        self,
        *,
        profile_id: str,
        automation_id: int,
        limit: int = 20,
    ) -> list[AutomationGraphRunMetadata]:
        """List recent graph runs for one automation."""

        async def _op(repo: AutomationGraphRepository) -> list[AutomationGraphRunMetadata]:
            automation = await repo.get_automation(profile_id=profile_id, automation_id=automation_id)
            if automation is None or automation.status == "deleted":
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            rows = await repo.list_runs(
                profile_id=profile_id,
                automation_id=automation_id,
                limit=limit,
            )
            return [_to_run_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def get_run(self, *, profile_id: str, run_id: int) -> AutomationGraphRunMetadata:
        """Return one graph run metadata row."""

        async def _op(repo: AutomationGraphRepository) -> AutomationGraphRunMetadata:
            row = await repo.get_run(profile_id=profile_id, run_id=run_id)
            if row is None:
                raise AutomationsServiceError(
                    error_code="automation_graph_run_not_found",
                    reason="Automation graph run not found",
                )
            return _to_run_metadata(row)

        return await self._with_repo(_op)

    async def get_trace(self, *, profile_id: str, run_id: int) -> AutomationGraphTraceMetadata:
        """Return one run plus ordered node trace payload."""

        async def _op(repo: AutomationGraphRepository) -> AutomationGraphTraceMetadata:
            run = await repo.get_run(profile_id=profile_id, run_id=run_id)
            if run is None:
                raise AutomationsServiceError(
                    error_code="automation_graph_run_not_found",
                    reason="Automation graph run not found",
                )
            node_runs = await repo.list_node_runs(run_id=run_id)
            node_trace = [_to_node_run_metadata(item) for item in node_runs]
            return AutomationGraphTraceMetadata(
                run=_to_run_metadata(run),
                nodes=node_trace,
                fallback=_to_fallback_trace_metadata(run=run, nodes=node_trace),
            )

        return await self._with_repo(_op)

    async def execute_triggered_automation(
        self,
        *,
        profile_id: str,
        automation_id: int,
        trigger_type: str,
        trigger_payload: Mapping[str, object],
        parent_session_id: str | None,
        context_overrides: TurnContextOverrides | None,
        event_hash: str | None,
        cron_expr: str | None,
        session_runner_factory: AutomationSessionRunnerFactory | None,
        run_timeout_sec: float | None,
    ) -> AutomationGraphRunMetadata:
        """Execute one active graph for the selected automation."""

        graph = await self._load_graph(profile_id=profile_id, automation_id=automation_id)
        execution_settings = get_profile_runtime_config_service(self._settings).build_effective_settings(
            profile_id=profile_id,
            base_settings=self._settings,
            ensure_layout=True,
        )
        started_at = datetime.now(timezone.utc)

        async def _bootstrap(repo: AutomationGraphRepository) -> int:
            run = await repo.create_run(
                automation_id=automation_id,
                flow_id=graph.flow.id,
                profile_id=profile_id,
                trigger_type=trigger_type,
                status="running",
                parent_session_id=parent_session_id,
                event_hash=event_hash,
                payload_json=_json_dumps(trigger_payload),
                started_at=started_at,
            )
            await repo.create_pending_node_runs(run_id=run.id, nodes=list(graph.nodes))
            return run.id

        run_id = await self._with_repo(_bootstrap)
        executor = AutomationGraphExecutor(
            settings=execution_settings,
            session_factory=self._session_factory,
            with_repo=self._with_repo,
            registry=self._registry,
        )
        try:
            outcome = await executor.execute(
                graph=graph,
                context=GraphExecutionContext(
                    run_id=run_id,
                    automation_id=automation_id,
                    profile_id=profile_id,
                    automation_prompt=graph.automation.prompt,
                    trigger_type="webhook" if trigger_type == "webhook" else "cron",
                    trigger_payload=dict(trigger_payload),
                    parent_session_id=parent_session_id,
                    context_overrides=context_overrides,
                    event_hash=event_hash,
                    cron_expr=cron_expr,
                    settings=execution_settings,
                    session_factory=self._session_factory,
                    session_runner_factory=session_runner_factory,
                    subagent_service_factory=self._subagent_service_factory,
                    timeout_sec=run_timeout_sec,
                ),
            )
        except Exception as exc:
            runtime_reason = _format_fallback_error(exc)
            await self._with_repo(
                lambda repo: repo.fail_run(
                    run_id=run_id,
                    status="failed",
                    error_code="automation_graph_runtime_error",
                    reason=runtime_reason,
                    fallback_status="not_attempted",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            raise AutomationsServiceError(
                error_code="automation_graph_runtime_error",
                reason=runtime_reason,
            ) from exc
        if outcome.status == "succeeded":
            await self._with_repo(
                lambda repo: repo.complete_run(
                    run_id=run_id,
                    status="succeeded",
                    final_output_json=_json_dumps(outcome.final_output)
                    if outcome.final_output is not None
                    else None,
                    fallback_status=None,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            return await self.get_run(profile_id=profile_id, run_id=run_id)

        fallback_mode = _runtime_fallback_mode(graph.automation.graph_fallback_mode)
        if fallback_mode == "fail_closed":
            await self._with_repo(
                lambda repo: repo.fail_run(
                    run_id=run_id,
                    status="failed",
                    error_code=outcome.error_code or "automation_graph_failed",
                    reason=outcome.reason or "Automation graph execution failed",
                    fallback_status="not_attempted",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            raise AutomationsServiceError(
                error_code=outcome.error_code or "automation_graph_failed",
                reason=outcome.reason or "Automation graph execution failed",
            )
        if fallback_mode == "resume_with_ai_if_safe" and outcome.unsafe_side_effects:
            await self._with_repo(
                lambda repo: repo.fail_run(
                    run_id=run_id,
                    status="failed",
                    error_code=outcome.error_code or "automation_graph_failed",
                    reason=outcome.reason or "Automation graph execution failed",
                    fallback_status="skipped_unsafe",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            raise AutomationsServiceError(
                error_code=outcome.error_code or "automation_graph_failed",
                reason=outcome.reason or "Automation graph execution failed",
            )
        try:
            fallback_result = await self._run_prompt_fallback(
                graph=graph,
                run_id=run_id,
                trigger_type="webhook" if trigger_type == "webhook" else "cron",
                trigger_payload=trigger_payload,
                parent_session_id=parent_session_id,
                context_overrides=context_overrides,
                session_runner_factory=session_runner_factory,
                outcome=outcome,
            )
        except Exception as exc:
            fallback_reason = _format_fallback_error(exc)
            await self._with_repo(
                lambda repo: repo.fail_run(
                    run_id=run_id,
                    status="fallback_failed",
                    error_code="automation_graph_fallback_failed",
                    reason=fallback_reason,
                    fallback_status="failed",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            raise AutomationsServiceError(
                error_code="automation_graph_fallback_failed",
                reason=fallback_reason,
            ) from exc
        await self._with_repo(
            lambda repo: repo.complete_run(
                run_id=run_id,
                status="fallback_succeeded",
                final_output_json=_json_dumps({"fallback": fallback_result}),
                fallback_status="succeeded",
                completed_at=datetime.now(timezone.utc),
            )
        )
        return await self.get_run(profile_id=profile_id, run_id=run_id)

    def validate_spec(self, spec: AutomationGraphSpec) -> AutomationGraphValidationReport:
        """Validate one graph write spec before persisting."""

        return _validate_spec(
            spec=spec,
            adapters=self._registry.available(),
        )

    def validate_loaded_graph(self, graph: LoadedGraph) -> AutomationGraphValidationReport:
        """Validate one loaded graph snapshot."""

        node_by_id = {node.id: node for node in graph.nodes}
        try:
            spec = AutomationGraphSpec.model_validate(
                {
                    "name": graph.flow.name,
                    "nodes": [_loaded_node_spec(node=node, graph=graph) for node in graph.nodes],
                    "edges": [
                        {
                            "source_key": node_by_id[edge.source_node_id].node_key,
                            "target_key": node_by_id[edge.target_node_id].node_key,
                            "source_port": edge.source_port,
                            "target_port": edge.target_port,
                        }
                        for edge in graph.edges
                    ],
                }
            )
        except ValidationError as exc:
            return AutomationGraphValidationReport(
                valid=False,
                errors=_validation_errors_from_exception(exc),
            )
        return _validate_spec(
            spec=spec,
            adapters=self._registry.available(),
        )

    async def _run_prompt_fallback(
        self,
        *,
        graph: LoadedGraph,
        run_id: int,
        trigger_type: Literal["cron", "webhook"],
        trigger_payload: Mapping[str, object],
        parent_session_id: str | None,
        context_overrides: TurnContextOverrides | None,
        session_runner_factory: AutomationSessionRunnerFactory | None,
        outcome: GraphExecutionOutcome,
    ) -> object:
        runner = build_automation_session_runner(
            session_factory=self._session_factory,
            profile_id=graph.automation.profile_id,
            settings=self._settings,
            runner_factory=session_runner_factory,
        )
        trace = await self.get_trace(profile_id=graph.automation.profile_id, run_id=run_id)
        if trigger_type == "webhook":
            base = compose_webhook_message(graph.automation.prompt, sanitize_payload(trigger_payload))
        else:
            base = compose_cron_message(graph.automation.prompt)
        message = (
            f"{base}\n\n"
            "graph_fallback_context="
            + json.dumps(
                {
                    "run_id": run_id,
                    "graph_status": outcome.status,
                    "graph_error_code": outcome.error_code,
                    "graph_reason": outcome.reason,
                    "unsafe_side_effects": outcome.unsafe_side_effects,
                    "node_trace": [
                        {
                            "node_key": item.node_key,
                            "execution_index": item.execution_index,
                            "status": item.status,
                            "selected_ports": item.selected_ports,
                            "output": _fallback_node_output(item),
                            "output_redacted": _fallback_node_output_redacted(item),
                            "effects": [effect.model_dump(mode="python") for effect in item.effects],
                            "error_code": item.error_code,
                            "reason": item.reason,
                            "child_task_id": item.child_task_id,
                            "child_session_id": item.child_session_id,
                            "child_run_id": item.child_run_id,
                        }
                        for item in trace.nodes
                    ],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        result = await runner.run_turn(
            profile_id=graph.automation.profile_id,
            session_id=parent_session_id
            or f"automation-graph-fallback-{graph.automation.id}-{run_id}",
            message=message,
            context_overrides=context_overrides,
            source="automation",
        )
        return _normalize_result(result)

    async def _load_graph(self, *, profile_id: str, automation_id: int) -> LoadedGraph:
        async def _op(repo: AutomationGraphRepository) -> LoadedGraph:
            automation = await repo.get_automation(profile_id=profile_id, automation_id=automation_id)
            if automation is None or automation.status == "deleted":
                raise AutomationsServiceError(
                    error_code="automation_not_found",
                    reason="Automation not found",
                )
            flow = await repo.get_active_flow(automation_id=automation_id)
            if flow is None:
                raise AutomationsServiceError(
                    error_code="automation_graph_missing",
                    reason="Automation graph not found",
                )
            nodes = await repo.list_nodes(flow_id=flow.id)
            edges = await repo.list_edges(flow_id=flow.id)
            version_ids = tuple(
                sorted(node.node_version_id for node in nodes if node.node_version_id is not None)
            )
            versions = await repo.list_versions(version_ids=version_ids)
            schemas_by_node_id = _prepare_loaded_schemas(nodes=nodes, versions=versions)
            return LoadedGraph(
                automation=automation,
                flow=flow,
                nodes=tuple(nodes),
                edges=tuple(edges),
                versions_by_id=versions,
                schemas_by_node_id=schemas_by_node_id,
            )

        return await self._with_repo(_op)

    async def _with_repo(
        self,
        op: Callable[[AutomationGraphRepository], Awaitable[TRepoValue]],
    ) -> TRepoValue:
        async with session_scope(self._session_factory) as session:
            return await op(AutomationGraphRepository(session))


def _validate_nodes_and_edges(
    *,
    node_keys: list[str],
    edges: list[tuple[str, str]],
    nodes_present: bool,
) -> AutomationGraphValidationReport:
    errors: list[str] = []
    if not nodes_present:
        errors.append("graph must include at least one node")
        return AutomationGraphValidationReport(valid=False, errors=errors)
    seen: set[str] = set()
    for key in node_keys:
        if key in seen:
            errors.append(f"duplicate node key: {key}")
        seen.add(key)
    for source_key, target_key in edges:
        if source_key not in seen:
            errors.append(f"edge references unknown source node: {source_key}")
        if target_key not in seen:
            errors.append(f"edge references unknown target node: {target_key}")
    adjacency: dict[str, list[str]] = defaultdict(list)
    incoming_count: dict[str, int] = {key: 0 for key in seen}
    for source_key, target_key in edges:
        if source_key in seen and target_key in seen:
            adjacency[source_key].append(target_key)
            incoming_count[target_key] += 1
    queue: deque[str] = deque(sorted(key for key, count in incoming_count.items() if count == 0))
    visited: list[str] = []
    while queue:
        key = queue.popleft()
        visited.append(key)
        for target in adjacency.get(key, []):
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                queue.append(target)
    if len(visited) != len(seen):
        errors.append("graph contains one cycle or unreachable strongly connected component")
    return AutomationGraphValidationReport(valid=not errors, errors=errors)


def _validate_spec(
    *,
    spec: AutomationGraphSpec,
    adapters: Mapping[tuple[str, str], object],
) -> AutomationGraphValidationReport:
    report = _validate_nodes_and_edges(
        node_keys=[node.key for node in spec.nodes],
        edges=[(edge.source_key, edge.target_key) for edge in spec.edges],
        nodes_present=bool(spec.nodes),
    )
    errors = list(report.errors)
    for node in spec.nodes:
        node_pair = (node.node_kind, node.node_type)
        adapter = adapters.get(node_pair)
        if adapter is None:
            errors.append(f"unsupported node adapter: {node.node_kind}/{node.node_type}")
            continue
        if node.node_kind == "code":
            if node.version is None:
                errors.append(f"code node `{node.key}` requires one inline version spec")
            else:
                runtime = node.version.runtime.strip().lower()
                if runtime != "python":
                    errors.append(f"code node `{node.key}` runtime must be python")
                if not node.version.source_code.strip():
                    errors.append(f"code node `{node.key}` requires non-empty source_code")
                for schema_name, schema_payload in (
                    ("config_schema", node.version.config_schema),
                    ("input_schema", node.version.input_schema),
                    ("output_schema", node.version.output_schema),
                ):
                    if schema_payload is None:
                        continue
                    try:
                        Draft202012Validator.check_schema(schema_payload)
                    except SchemaError as exc:
                        errors.append(
                            f"code node `{node.key}` has invalid {schema_name}: {exc.message}"
                        )
        if node.node_kind in {"ai", "agent"} and not str(node.config.get("prompt") or "").strip():
            errors.append(f"{node.node_kind} node `{node.key}` requires config.prompt")
        if node.node_kind == "agent" and "timeout_sec" in node.config:
            try:
                timeout_sec = int(str(node.config["timeout_sec"]).strip())
            except ValueError:
                errors.append(f"agent node `{node.key}` timeout_sec must be an integer")
            else:
                if timeout_sec <= 0:
                    errors.append(f"agent node `{node.key}` timeout_sec must be > 0")
        validator = getattr(adapter, "validate_spec", None)
        if callable(validator):
            errors.extend(str(item) for item in validator(node.model_dump(mode="python")))
    return AutomationGraphValidationReport(valid=not errors, errors=errors)

def _runtime_fallback_mode(value: str) -> Literal["fail_closed", "resume_with_ai", "resume_with_ai_if_safe"]:
    """Collapse any legacy fallback values onto the implemented runtime surface."""

    if value == "branch_error_only":
        return "fail_closed"
    if value == "fail_closed":
        return "fail_closed"
    if value == "resume_with_ai":
        return "resume_with_ai"
    if value == "resume_with_ai_if_safe":
        return "resume_with_ai_if_safe"
    raise AutomationsServiceError(
        error_code="invalid_graph_fallback_mode",
        reason=f"Unsupported graph fallback mode: {value}",
    )


def _loaded_node_spec(*, node: AutomationNode, graph: LoadedGraph) -> dict[str, object]:
    payload: dict[str, object] = {
        "key": node.node_key,
        "name": node.name,
        "node_kind": node.node_kind,
        "node_type": node.node_type,
        "config": _parse_json(node.config_json) or {},
    }
    version = graph.versions_by_id.get(node.node_version_id or -1)
    if version is not None:
        payload["version"] = {
            "runtime": version.runtime or "",
            "version_label": version.version_label,
            "source_code": version.source_code or "",
            "config_schema": _parse_json(version.config_schema_json),
            "input_schema": _parse_json(version.input_schema_json),
            "output_schema": _parse_json(version.output_schema_json),
            "manifest": _parse_json(version.manifest_json) or {},
            "tests": _parse_json(version.tests_json),
        }
    return payload


def _prepare_loaded_schemas(
    *,
    nodes: list[AutomationNode],
    versions: Mapping[int, object],
) -> dict[int, LoadedNodeSchemas]:
    schemas_by_node_id: dict[int, LoadedNodeSchemas] = {}
    for node in nodes:
        version = versions.get(node.node_version_id or -1)
        if version is None:
            continue
        input_schema = _validated_schema_document(
            raw=getattr(version, "input_schema_json", None),
            node_key=node.node_key,
            schema_name="input_schema",
        )
        output_schema = _validated_schema_document(
            raw=getattr(version, "output_schema_json", None),
            node_key=node.node_key,
            schema_name="output_schema",
        )
        if input_schema is None and output_schema is None:
            continue
        schemas_by_node_id[node.id] = LoadedNodeSchemas(
            input_schema=input_schema,
            output_schema=output_schema,
        )
    return schemas_by_node_id


def _validated_schema_document(
    *,
    raw: str | None,
    node_key: str,
    schema_name: str,
) -> dict[str, object] | None:
    payload = _parse_json(raw)
    if payload is None:
        return None
    try:
        Draft202012Validator.check_schema(payload)
    except SchemaError as exc:
        raise AutomationsServiceError(
            error_code="invalid_graph_spec",
            reason=f"code node `{node_key}` has invalid {schema_name}: {exc.message}",
        ) from exc
    return payload


def _to_graph_metadata(graph: LoadedGraph) -> AutomationGraphMetadata:
    node_by_id = {node.id: node for node in graph.nodes}
    return AutomationGraphMetadata(
        flow_id=graph.flow.id,
        automation_id=graph.automation.id,
        execution_mode=as_execution_mode(graph.automation.execution_mode),
        graph_fallback_mode=_runtime_fallback_mode(graph.automation.graph_fallback_mode),
        name=graph.flow.name,
        version=graph.flow.version,
        status=graph.flow.status,
        nodes=[
            AutomationGraphNodeMetadata(
                id=node.id,
                key=node.node_key,
                name=node.name,
                node_kind=_as_graph_node_kind(node.node_kind),
                node_type=node.node_type,
                config=_parse_json(node.config_json) or {},
                node_version_id=node.node_version_id,
            )
            for node in graph.nodes
        ],
        edges=[
            AutomationGraphEdgeMetadata(
                id=edge.id,
                source_key=node_by_id[edge.source_node_id].node_key,
                target_key=node_by_id[edge.target_node_id].node_key,
                source_port=edge.source_port,
                target_port=edge.target_port,
            )
            for edge in graph.edges
        ],
    )


def _to_run_metadata(row: AutomationRun) -> AutomationGraphRunMetadata:
    return AutomationGraphRunMetadata(
        id=row.id,
        automation_id=row.automation_id,
        flow_id=row.flow_id,
        profile_id=row.profile_id,
        trigger_type=row.trigger_type,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        parent_session_id=row.parent_session_id,
        event_hash=row.event_hash,
        fallback_status=row.fallback_status,
        error_code=row.error_code,
        reason=row.reason,
        final_output=_parse_json(row.final_output_json),
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _to_node_run_metadata(row: AutomationNodeRun) -> AutomationGraphNodeRunMetadata:
    return AutomationGraphNodeRunMetadata(
        id=row.id,
        node_id=row.node_id,
        node_key=row.node_key,
        status=row.status,  # type: ignore[arg-type]
        attempt=row.attempt,
        execution_index=row.execution_index,
        selected_ports=_parse_json_list(row.selected_ports_json),
        effects=_parse_effects_json(row.effects_json),
        input=_parse_json(row.input_json) or {},
        output=_parse_json(row.output_json),
        error_code=row.error_code,
        reason=row.reason,
        child_task_id=row.child_task_id,
        child_session_id=row.child_session_id,
        child_run_id=row.child_run_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _to_fallback_trace_metadata(
    *,
    run: AutomationRun,
    nodes: list[AutomationGraphNodeRunMetadata],
) -> AutomationGraphFallbackTraceMetadata | None:
    if not run.fallback_status or run.fallback_status == "not_attempted":
        return None
    max_execution_index = max((item.execution_index or 0) for item in nodes) if nodes else 0
    return AutomationGraphFallbackTraceMetadata(
        execution_index=max_execution_index + 1,
        status=run.fallback_status,
        error_code=run.error_code,
        reason=run.reason,
        output=_parse_json(run.final_output_json),
    )


def _parse_json(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): value for key, value in parsed.items()}


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _parse_effects_json(raw: str | None) -> list[AutomationGraphNodeEffectMetadata]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    effects: list[AutomationGraphNodeEffectMetadata] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            effects.append(AutomationGraphNodeEffectMetadata.model_validate(item))
        except ValidationError:
            continue
    return effects


def _fallback_node_output(item: AutomationGraphNodeRunMetadata) -> dict[str, object] | None:
    if item.output is None:
        return None
    if not _fallback_node_output_redacted(item):
        return item.output
    default_payload = item.output.get("default")
    if len(item.output) == 1 and isinstance(default_payload, dict):
        return {
            "redacted": True,
            "ports": ["default"],
            "keys": sorted(str(key) for key in default_payload.keys()),
        }
    return {
        "redacted": True,
        "keys": sorted(item.output.keys()),
    }


def _fallback_node_output_redacted(item: AutomationGraphNodeRunMetadata) -> bool:
    return any(effect.committed and effect.safety_class == "unsafe" for effect in item.effects)


def _validation_errors_from_exception(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg") or "Invalid value")
        errors.append(f"{location}: {message}" if location else message)
    return errors or ["Persisted graph contains one invalid node or edge payload"]


def _as_graph_node_kind(value: str) -> Literal["builtin", "code", "ai", "agent", "task", "action"]:
    if value in {"builtin", "code", "ai", "agent", "task", "action"}:
        return value  # type: ignore[return-value]
    raise AutomationsServiceError(
        error_code="invalid_graph_node_kind",
        reason=f"Unsupported graph node kind: {value}",
    )


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _normalize_result(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): inner for key, inner in value.items()}
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _format_fallback_error(exc: Exception) -> str:
    if isinstance(exc, AutomationsServiceError):
        return str(sanitize_payload_value(f"{exc.error_code}: {exc.reason}", field_name="reason"))[:2000]
    message = str(exc).strip()
    if message:
        return str(
            sanitize_payload_value(f"{type(exc).__name__}: {message}", field_name="reason")
        )[:2000]
    return str(sanitize_payload_value(type(exc).__name__, field_name="reason"))[:2000]
