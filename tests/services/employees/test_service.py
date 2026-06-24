"""Tests for profile-scoped Task Flow employee descriptors."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.employees import EmployeeService, EmployeeServiceError
from afkbot.services.task_flow import TaskFlowService
from afkbot.services.task_flow.human_ref import resolve_local_human_ref
from afkbot.settings import Settings
from tests.repositories._harness import build_repository_factory


async def test_employee_service_upserts_and_lists_profile_employees(tmp_path: Path) -> None:
    """Employees should be markdown-backed, profile-scoped descriptors."""

    service = EmployeeService(Settings(root_dir=tmp_path))

    created = await service.upsert_employee(
        profile_id="default",
        employee_id="backend-lead",
        content="\n".join(
            [
                "---",
                "id: backend-lead",
                "name: Backend Lead",
                "title: Team Lead Backend",
                "role: team_lead",
                "status: active",
                "manager_id: cto",
                "reports:",
                "  - backend-developer",
                "allowed_tools:",
                "  - task.*",
                "  - subagent.run",
                "can_use_subagents: true",
                "subagent_allowlist:",
                "  - reviewer",
                "max_active_tasks: 1",
                "---",
                "",
                "# Backend Lead",
                "",
                "Own backend delivery and delegate implementation tasks.",
            ]
        ),
    )

    assert created.id == "backend-lead"
    assert created.profile_id == "default"
    assert created.name == "Backend Lead"
    assert created.manager_id == "cto"
    assert created.reports == ("backend-developer",)
    assert created.allowed_tools == ("task.*", "subagent.run")
    assert created.can_use_subagents is True
    assert created.subagent_allowlist == ("reviewer",)
    assert created.max_active_tasks == 1
    assert "Own backend delivery" in created.body

    items = await service.list_employees(profile_id="default")
    assert [item.id for item in items] == ["backend-lead"]

    loaded = await service.get_employee(profile_id="default", employee_id="backend-lead")
    assert loaded == created


async def test_employee_service_rejects_unsafe_and_mismatched_ids(tmp_path: Path) -> None:
    """Employee ids must be safe profile-local slugs and match the file stem."""

    service = EmployeeService(Settings(root_dir=tmp_path))

    with pytest.raises(EmployeeServiceError) as invalid_id:
        await service.upsert_employee(
            profile_id="default",
            employee_id="backend_lead",
            content="# Backend Lead",
        )
    assert invalid_id.value.error_code == "invalid_employee_id"

    with pytest.raises(EmployeeServiceError) as mismatched:
        await service.upsert_employee(
            profile_id="default",
            employee_id="backend-lead",
            content="\n".join(
                [
                    "---",
                    "id: other-lead",
                    "name: Other",
                    "title: Other",
                    "role: lead",
                    "status: active",
                    "---",
                    "# Other",
                ]
            ),
        )
    assert mismatched.value.error_code == "employee_id_mismatch"

    with pytest.raises(EmployeeServiceError) as invalid_profile:
        await service.list_employees(profile_id="../default")
    assert invalid_profile.value.error_code == "invalid_profile_id"


async def test_employee_org_validation_reports_missing_refs_and_cycles(tmp_path: Path) -> None:
    """Validation should make broken hierarchy and delegation explicit."""

    service = EmployeeService(Settings(root_dir=tmp_path))
    await service.upsert_employee(
        profile_id="default",
        employee_id="cto",
        content="\n".join(
            [
                "---",
                "id: cto",
                "name: CTO",
                "title: CTO",
                "role: cto",
                "status: active",
                "manager_id: qa",
                "can_delegate_to:",
                "  - missing-developer",
                "---",
                "# CTO",
            ]
        ),
    )
    await service.upsert_employee(
        profile_id="default",
        employee_id="qa",
        content="\n".join(
            [
                "---",
                "id: qa",
                "name: QA",
                "title: QA Engineer",
                "role: qa",
                "status: active",
                "manager_id: cto",
                "---",
                "# QA",
            ]
        ),
    )

    report = await service.validate_org_chart(profile_id="default")
    issue_codes = {issue.code for issue in report.issues}

    assert "employee_hierarchy_cycle" in issue_codes
    assert "employee_missing_delegate" in issue_codes
    assert report.valid is False


async def test_employee_org_chart_derives_reports_from_manager_ids(tmp_path: Path) -> None:
    """Org chart should derive reporting edges without requiring duplicate reports lists."""

    service = EmployeeService(Settings(root_dir=tmp_path))
    await service.upsert_employee(
        profile_id="default",
        employee_id="cto",
        content="\n".join(
            [
                "---",
                "id: cto",
                "name: CTO",
                "title: CTO",
                "role: cto",
                "status: active",
                "---",
                "# CTO",
            ]
        ),
    )
    await service.upsert_employee(
        profile_id="default",
        employee_id="backend-lead",
        content="\n".join(
            [
                "---",
                "id: backend-lead",
                "name: Backend Lead",
                "title: Team Lead Backend",
                "role: team_lead",
                "status: active",
                "manager_id: cto",
                "---",
                "# Backend Lead",
            ]
        ),
    )

    chart = await service.build_org_chart(profile_id="default")

    assert chart.root_employee_ids == ("cto",)
    assert chart.edges == (("cto", "backend-lead"),)
    assert chart.employees["cto"].derived_reports == ("backend-lead",)
    assert chart.validation.valid is True


async def test_employee_service_blocks_delete_when_org_chart_references_employee(
    tmp_path: Path,
) -> None:
    """Deleting an employee must not orphan manager/report/delegate relationships."""

    service = EmployeeService(Settings(root_dir=tmp_path))
    await service.upsert_employee(
        profile_id="default",
        employee_id="cto",
        content="\n".join(
            [
                "---",
                "id: cto",
                "name: CTO",
                "title: CTO",
                "role: cto",
                "status: active",
                "reports:",
                "  - backend-lead",
                "---",
                "# CTO",
            ]
        ),
    )
    await service.upsert_employee(
        profile_id="default",
        employee_id="backend-lead",
        content="\n".join(
            [
                "---",
                "id: backend-lead",
                "name: Backend Lead",
                "title: Team Lead Backend",
                "role: team_lead",
                "status: active",
                "manager_id: cto",
                "---",
                "# Backend Lead",
            ]
        ),
    )

    with pytest.raises(EmployeeServiceError) as exc_info:
        await service.delete_employee(profile_id="default", employee_id="cto")

    assert exc_info.value.error_code == "employee_in_use"
    assert "manager_of:backend-lead" in exc_info.value.reason
    assert "report:backend-lead" in exc_info.value.reason
    assert (tmp_path / "profiles" / "default" / "employees" / "cto.md").is_file()


async def test_employee_service_hides_out_of_scope_symlink(tmp_path: Path) -> None:
    """Employee discovery must not follow symlinks outside a profile employee directory."""

    outside = tmp_path / "outside/cto.md"
    outside.parent.mkdir(parents=True)
    outside.write_text(
        "---\nid: cto\nname: CTO\ntitle: CTO\nrole: cto\nstatus: active\n---\n# CTO",
        encoding="utf-8",
    )
    employees_root = tmp_path / "profiles/default/employees"
    employees_root.mkdir(parents=True)
    try:
        (employees_root / "cto.md").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are not supported in this environment")

    service = EmployeeService(Settings(root_dir=tmp_path))

    assert await service.list_employees(profile_id="default") == []
    with pytest.raises(EmployeeServiceError) as exc_info:
        await service.get_employee(profile_id="default", employee_id="cto")
    assert exc_info.value.error_code == "employee_not_found"


async def test_employee_service_blocks_delete_when_task_flow_references_employee(
    tmp_path: Path,
) -> None:
    """Deleting an employee must not leave Task Flow owner/reviewer references dangling."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="employees.db",
        profile_ids=("default",),
    )
    settings = getattr(factory, "_afkbot_test_settings")
    task_service = TaskFlowService(factory, settings=settings, engine=engine)
    employee_service = EmployeeService(settings, session_factory=factory)
    await task_service.create_task(
        profile_id="default",
        flow_id=None,
        title="Referenced employee",
        description="Keep employee descriptor while tasks refer to it.",
        status="todo",
        priority=0,
        owner_type="employee",
        owner_ref="analyst",
        reviewer_type=None,
        reviewer_ref=None,
        created_by_type="human",
        created_by_ref=resolve_local_human_ref(settings),
    )

    with pytest.raises(EmployeeServiceError) as exc_info:
        await employee_service.delete_employee(profile_id="default", employee_id="analyst")

    assert exc_info.value.error_code == "employee_in_use"
    assert (settings.profiles_dir / "default" / "employees" / "analyst.md").is_file()
    await engine.dispose()
