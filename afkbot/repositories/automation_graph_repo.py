"""Repository for automation graph persistence and execution ledgers."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.automation import Automation
from afkbot.models.automation_edge import AutomationEdge
from afkbot.models.automation_flow import AutomationFlow
from afkbot.models.automation_node import AutomationNode
from afkbot.models.automation_node_definition import AutomationNodeDefinition
from afkbot.models.automation_node_run import AutomationNodeRun
from afkbot.models.automation_node_version import AutomationNodeVersion
from afkbot.models.automation_run import AutomationRun


class AutomationGraphRepository:
    """Persistence operations for automation graph definitions and traces."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_automation(self, *, profile_id: str, automation_id: int) -> Automation | None:
        statement: Select[tuple[Automation]] = select(Automation).where(
            Automation.profile_id == profile_id,
            Automation.id == automation_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def set_automation_execution_mode(
        self,
        *,
        profile_id: str,
        automation_id: int,
        execution_mode: str,
    ) -> Automation | None:
        automation = await self.get_automation(profile_id=profile_id, automation_id=automation_id)
        if automation is None:
            return None
        automation.execution_mode = execution_mode
        await self._session.flush()
        await self._session.refresh(automation)
        return automation

    async def list_flows(self, *, automation_id: int) -> list[AutomationFlow]:
        statement: Select[tuple[AutomationFlow]] = (
            select(AutomationFlow)
            .where(AutomationFlow.automation_id == automation_id)
            .order_by(AutomationFlow.version.asc(), AutomationFlow.id.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def archive_active_flows(self, *, automation_id: int) -> None:
        flows = await self.list_flows(automation_id=automation_id)
        for flow in flows:
            if flow.status == "active":
                flow.status = "archived"
        await self._session.flush()

    async def create_flow(
        self,
        *,
        automation_id: int,
        name: str,
        version: int,
        status: str = "active",
    ) -> AutomationFlow:
        flow = AutomationFlow(
            automation_id=automation_id,
            name=name,
            status=status,
            version=version,
        )
        self._session.add(flow)
        await self._session.flush()
        await self._session.refresh(flow)
        return flow

    async def get_active_flow(self, *, automation_id: int) -> AutomationFlow | None:
        statement: Select[tuple[AutomationFlow]] = (
            select(AutomationFlow)
            .where(
                AutomationFlow.automation_id == automation_id,
                AutomationFlow.status == "active",
            )
            .order_by(AutomationFlow.version.desc(), AutomationFlow.id.desc())
        )
        return (await self._session.execute(statement)).scalars().first()

    async def get_definition(
        self,
        *,
        profile_id: str,
        slug: str,
    ) -> AutomationNodeDefinition | None:
        statement: Select[tuple[AutomationNodeDefinition]] = select(AutomationNodeDefinition).where(
            AutomationNodeDefinition.profile_id == profile_id,
            AutomationNodeDefinition.slug == slug,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def create_definition(
        self,
        *,
        profile_id: str,
        slug: str,
        node_kind: str,
        node_type: str,
        display_name: str,
    ) -> AutomationNodeDefinition:
        definition = AutomationNodeDefinition(
            profile_id=profile_id,
            slug=slug,
            node_kind=node_kind,
            node_type=node_type,
            display_name=display_name,
        )
        self._session.add(definition)
        await self._session.flush()
        await self._session.refresh(definition)
        return definition

    async def count_versions(self, *, node_definition_id: int) -> int:
        statement = select(func.count(AutomationNodeVersion.id)).where(
            AutomationNodeVersion.node_definition_id == node_definition_id
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def create_version(
        self,
        *,
        node_definition_id: int,
        version_label: str,
        runtime: str | None,
        config_schema_json: str | None,
        input_schema_json: str | None,
        output_schema_json: str | None,
        manifest_json: str | None,
        tests_json: str | None,
        source_code: str | None,
    ) -> AutomationNodeVersion:
        version = AutomationNodeVersion(
            node_definition_id=node_definition_id,
            version_label=version_label,
            runtime=runtime,
            config_schema_json=config_schema_json,
            input_schema_json=input_schema_json,
            output_schema_json=output_schema_json,
            manifest_json=manifest_json,
            tests_json=tests_json,
            source_code=source_code,
        )
        self._session.add(version)
        await self._session.flush()
        await self._session.refresh(version)
        return version

    async def create_node(
        self,
        *,
        flow_id: int,
        node_key: str,
        name: str,
        node_kind: str,
        node_type: str,
        config_json: str,
        node_definition_id: int | None = None,
        node_version_id: int | None = None,
    ) -> AutomationNode:
        node = AutomationNode(
            flow_id=flow_id,
            node_key=node_key,
            name=name,
            node_kind=node_kind,
            node_type=node_type,
            config_json=config_json,
            node_definition_id=node_definition_id,
            node_version_id=node_version_id,
        )
        self._session.add(node)
        await self._session.flush()
        await self._session.refresh(node)
        return node

    async def create_edge(
        self,
        *,
        flow_id: int,
        source_node_id: int,
        target_node_id: int,
        source_port: str,
        target_port: str,
    ) -> AutomationEdge:
        edge = AutomationEdge(
            flow_id=flow_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            source_port=source_port,
            target_port=target_port,
        )
        self._session.add(edge)
        await self._session.flush()
        await self._session.refresh(edge)
        return edge

    async def list_nodes(self, *, flow_id: int) -> list[AutomationNode]:
        statement: Select[tuple[AutomationNode]] = (
            select(AutomationNode)
            .where(AutomationNode.flow_id == flow_id)
            .order_by(AutomationNode.id.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def list_edges(self, *, flow_id: int) -> list[AutomationEdge]:
        statement: Select[tuple[AutomationEdge]] = (
            select(AutomationEdge)
            .where(AutomationEdge.flow_id == flow_id)
            .order_by(AutomationEdge.id.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def list_versions(
        self,
        *,
        version_ids: tuple[int, ...],
    ) -> dict[int, AutomationNodeVersion]:
        if not version_ids:
            return {}
        statement: Select[tuple[AutomationNodeVersion]] = select(AutomationNodeVersion).where(
            AutomationNodeVersion.id.in_(version_ids)
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        return {item.id: item for item in rows}

    async def create_run(
        self,
        *,
        automation_id: int,
        flow_id: int | None,
        profile_id: str,
        trigger_type: str,
        status: str,
        parent_session_id: str | None,
        event_hash: str | None,
        payload_json: str | None,
        started_at: datetime,
    ) -> AutomationRun:
        run = AutomationRun(
            automation_id=automation_id,
            flow_id=flow_id,
            profile_id=profile_id,
            trigger_type=trigger_type,
            status=status,
            parent_session_id=parent_session_id,
            event_hash=event_hash,
            payload_json=payload_json,
            started_at=started_at,
        )
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def create_pending_node_runs(
        self,
        *,
        run_id: int,
        nodes: list[AutomationNode],
    ) -> list[AutomationNodeRun]:
        rows: list[AutomationNodeRun] = []
        for node in nodes:
            row = AutomationNodeRun(
                run_id=run_id,
                node_id=node.id,
                node_key=node.node_key,
                status="pending",
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        for row in rows:
            await self._session.refresh(row)
        return rows

    async def get_node_run(self, *, run_id: int, node_id: int) -> AutomationNodeRun | None:
        statement: Select[tuple[AutomationNodeRun]] = select(AutomationNodeRun).where(
            AutomationNodeRun.run_id == run_id,
            AutomationNodeRun.node_id == node_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def update_node_run(
        self,
        *,
        run_id: int,
        node_id: int,
        status: str,
        execution_index: int | None = None,
        effects_json: str | None = None,
        input_json: str | None = None,
        output_json: str | None = None,
        selected_ports_json: str | None = None,
        error_code: str | None = None,
        reason: str | None = None,
        child_task_id: str | None = None,
        child_session_id: str | None = None,
        child_run_id: int | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> AutomationNodeRun | None:
        row = await self.get_node_run(run_id=run_id, node_id=node_id)
        if row is None:
            return None
        row.status = status
        if execution_index is not None:
            row.execution_index = execution_index
        if effects_json is not None:
            row.effects_json = effects_json
        if input_json is not None:
            row.input_json = input_json
        if output_json is not None:
            row.output_json = output_json
        if selected_ports_json is not None:
            row.selected_ports_json = selected_ports_json
        if error_code is not None:
            row.error_code = error_code
        if reason is not None:
            row.reason = reason
        if child_task_id is not None:
            row.child_task_id = child_task_id
        if child_session_id is not None:
            row.child_session_id = child_session_id
        if child_run_id is not None:
            row.child_run_id = child_run_id
        if started_at is not None:
            row.started_at = started_at
        if completed_at is not None:
            row.completed_at = completed_at
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_runs(
        self,
        *,
        profile_id: str,
        automation_id: int,
        limit: int = 20,
    ) -> list[AutomationRun]:
        statement: Select[tuple[AutomationRun]] = (
            select(AutomationRun)
            .where(
                AutomationRun.profile_id == profile_id,
                AutomationRun.automation_id == automation_id,
            )
            .order_by(AutomationRun.id.desc())
            .limit(max(1, int(limit)))
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def get_run(self, *, profile_id: str, run_id: int) -> AutomationRun | None:
        statement: Select[tuple[AutomationRun]] = select(AutomationRun).where(
            AutomationRun.profile_id == profile_id,
            AutomationRun.id == run_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_node_runs(self, *, run_id: int) -> list[AutomationNodeRun]:
        statement: Select[tuple[AutomationNodeRun]] = (
            select(AutomationNodeRun)
            .where(AutomationNodeRun.run_id == run_id)
            .order_by(
                func.coalesce(AutomationNodeRun.execution_index, 2**31 - 1).asc(),
                AutomationNodeRun.id.asc(),
            )
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def complete_run(
        self,
        *,
        run_id: int,
        status: str,
        final_output_json: str | None,
        fallback_status: str | None,
        completed_at: datetime,
    ) -> AutomationRun | None:
        run = cast(AutomationRun | None, await self._session.get(AutomationRun, run_id))
        if run is None:
            return None
        run.status = status
        run.final_output_json = final_output_json
        run.fallback_status = fallback_status
        run.completed_at = completed_at
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def fail_run(
        self,
        *,
        run_id: int,
        status: str,
        error_code: str,
        reason: str,
        fallback_status: str | None,
        completed_at: datetime,
    ) -> AutomationRun | None:
        run = cast(AutomationRun | None, await self._session.get(AutomationRun, run_id))
        if run is None:
            return None
        run.status = status
        run.error_code = error_code
        run.reason = reason
        run.fallback_status = fallback_status
        run.completed_at = completed_at
        await self._session.flush()
        await self._session.refresh(run)
        return run
