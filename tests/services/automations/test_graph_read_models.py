"""Read-model coverage for automation graph inspection."""

from __future__ import annotations

from sqlalchemy import update

from afkbot.db.session import session_scope
from afkbot.models.automation import Automation
from afkbot.models.automation_node import AutomationNode
from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.graph.contracts import AutomationGraphNodeSpec, AutomationGraphSpec
from tests.services.automations._harness import prepare_service


async def test_graph_read_models_expose_nodes_edges_and_trace(tmp_path) -> None:
    """Terminal read models should expose stable graph and run-trace shapes."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-read-models",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="inspectable-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "finish"}],
            ),
        )

        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-trace", "kind": "demo"},
        )

        graph = await service.get_graph(profile_id="default", automation_id=created.id)
        assert graph.automation_id == created.id
        assert graph.execution_mode == "graph"
        assert [node.key for node in graph.nodes] == ["trigger", "finish"]
        assert [(edge.source_key, edge.target_key) for edge in graph.edges] == [("trigger", "finish")]

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        assert trace.run.id == run.id
        assert trace.run.status == "succeeded"
        assert trace.run.final_output == {
            "default": {"event_id": "evt-trace", "kind": "demo"}
        }
        assert trace.nodes[0].input == {}
        assert trace.nodes[1].output == {
            "default": {"event_id": "evt-trace", "kind": "demo"}
        }
    finally:
        await engine.dispose()


async def test_graph_trace_orders_nodes_by_execution_not_insert_order(tmp_path) -> None:
    """Trace ordering should reflect real execution order even if spec order differs."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-trace-order",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="trace-order-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "finish"}],
            ),
        )

        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-trace-order"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        assert [node.node_key for node in trace.nodes] == ["trigger", "finish"]
        assert trace.nodes[0].execution_index == 1
        assert trace.nodes[1].execution_index == 2
    finally:
        await engine.dispose()


async def test_graph_validate_returns_structured_report_for_invalid_persisted_node_kind(tmp_path) -> None:
    """Persisted-bad node rows should stay inspectable through graph-validate."""

    engine, factory, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-invalid-node-kind",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="invalid-node-kind-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                ],
                edges=[],
            ),
        )
        graph = await service.get_graph(profile_id="default", automation_id=created.id)
        async with session_scope(factory) as session:
            await session.execute(
                update(AutomationNode)
                .where(AutomationNode.flow_id == graph.flow_id)
                .values(node_kind="broken_kind")
            )

        report = await service.validate_graph(profile_id="default", automation_id=created.id)
        assert report.valid is False
        assert any("nodes.0.node_kind" in item for item in report.errors)
    finally:
        await engine.dispose()


async def test_graph_show_raises_domain_error_for_invalid_persisted_execution_mode(tmp_path) -> None:
    """Read models should raise stable domain errors for invalid persisted automation enums."""

    engine, factory, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-invalid-execution-mode",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="invalid-execution-mode-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                ],
                edges=[],
            ),
        )
        async with session_scope(factory) as session:
            await session.execute(
                update(Automation)
                .where(Automation.id == created.id)
                .values(execution_mode="broken_mode")
            )

        try:
            await service.get_graph(profile_id="default", automation_id=created.id)
        except AutomationsServiceError as exc:
            assert exc.error_code == "invalid_execution_mode"
        else:
            raise AssertionError("expected invalid_execution_mode")
    finally:
        await engine.dispose()
