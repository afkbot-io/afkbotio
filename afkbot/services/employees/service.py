"""Service for profile-scoped Task Flow employees."""

from __future__ import annotations

from typing import cast

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.services.employees.contracts import (
    EmployeeMetadata,
    EmployeeOrgChart,
    EmployeeStatus,
    EmployeeValidationReport,
)
from afkbot.services.employees.errors import EmployeeServiceError
from afkbot.services.employees.ids import validate_employee_id
from afkbot.services.employees.markdown_store import EmployeeMarkdownStore
from afkbot.services.employees.org_chart import build_org_chart, validate_employees
from afkbot.services.naming import normalize_runtime_name
from afkbot.services.skills.markdown import FrontmatterValue, parse_frontmatter, split_frontmatter
from afkbot.settings import Settings

_VALID_STATUSES = frozenset(("active", "disabled", "archived"))


class EmployeeService:
    """Load, validate, and persist Task Flow employee descriptors."""

    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._settings = settings
        self._store = EmployeeMarkdownStore(settings)
        self._session_factory = session_factory
        self._engine = engine

    async def upsert_employee(
        self,
        *,
        profile_id: str,
        employee_id: str,
        content: str,
    ) -> EmployeeMetadata:
        """Create or replace one profile employee markdown file."""

        normalized_employee_id = _validate_employee_id(employee_id)
        metadata = _parse_employee_markdown(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
            content=content,
        )
        await self._store.write_markdown(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
            content=_ensure_trailing_newline(content),
        )
        return metadata

    async def list_employees(self, *, profile_id: str) -> list[EmployeeMetadata]:
        """List valid employee descriptors for one profile."""

        entries = await self._store.list_markdown(profile_id)
        employees = [
            _parse_employee_markdown(
                profile_id=profile_id,
                employee_id=employee_id,
                content=content,
            )
            for employee_id, content in entries
        ]
        return sorted(employees, key=lambda employee: employee.id)

    async def get_employee(self, *, profile_id: str, employee_id: str) -> EmployeeMetadata:
        """Return one employee descriptor."""

        normalized_employee_id = _validate_employee_id(employee_id)
        content = await self._store.get_markdown(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
        )
        return _parse_employee_markdown(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
            content=content,
        )

    async def delete_employee(self, *, profile_id: str, employee_id: str) -> EmployeeMetadata:
        """Delete one employee descriptor and return its previous metadata."""

        normalized_employee_id = _validate_employee_id(employee_id)
        org_reference_blockers = await self._employee_org_reference_blockers(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
        )
        if org_reference_blockers:
            blockers = ", ".join(org_reference_blockers[:8])
            raise EmployeeServiceError(
                error_code="employee_in_use",
                reason=(
                    f"Employee {normalized_employee_id} is referenced by the organization "
                    f"chart: {blockers}. Reassign those relationships before deleting it."
                ),
            )
        if await self._has_taskflow_references(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
        ):
            raise EmployeeServiceError(
                error_code="employee_in_use",
                reason=(
                    f"Employee {normalized_employee_id} is referenced by existing Task Flow "
                    "tasks or flows. Reassign or remove those references before deleting it."
                ),
            )
        content = await self._store.delete_markdown(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
        )
        return _parse_employee_markdown(
            profile_id=profile_id,
            employee_id=normalized_employee_id,
            content=content,
        )

    async def _employee_org_reference_blockers(
        self,
        *,
        profile_id: str,
        employee_id: str,
    ) -> list[str]:
        employees = await self.list_employees(profile_id=profile_id)
        blockers: list[str] = []
        for employee in employees:
            if employee.id == employee_id:
                if employee.reports:
                    blockers.extend(f"report:{report_id}" for report_id in employee.reports)
                if employee.can_delegate_to:
                    blockers.extend(
                        f"delegate:{delegate_id}" for delegate_id in employee.can_delegate_to
                    )
                continue
            if employee.manager_id == employee_id:
                blockers.append(f"manager_of:{employee.id}")
            if employee_id in employee.reports:
                blockers.append(f"listed_report_by:{employee.id}")
            if employee_id in employee.can_delegate_to:
                blockers.append(f"delegate_of:{employee.id}")
        return sorted(dict.fromkeys(blockers))

    async def validate_org_chart(self, *, profile_id: str) -> EmployeeValidationReport:
        """Validate all employee descriptors for one profile."""

        employees = await self.list_employees(profile_id=profile_id)
        return validate_employees(profile_id=profile_id, employees=employees)

    async def build_org_chart(self, *, profile_id: str) -> EmployeeOrgChart:
        """Build a resolved org chart for one profile."""

        employees = await self.list_employees(profile_id=profile_id)
        return build_org_chart(profile_id=profile_id, employees=employees)

    async def _has_taskflow_references(self, *, profile_id: str, employee_id: str) -> bool:
        session_factory = self._session_factory
        owned_engine: AsyncEngine | None = None
        if session_factory is None:
            engine = self._engine
            if engine is None:
                owned_engine = create_engine(self._settings)
                engine = owned_engine
            session_factory = create_session_factory(engine)
        try:
            from afkbot.repositories.task_flow_repo import TaskFlowRepository

            async with session_scope(session_factory) as session:
                return await TaskFlowRepository(session).employee_has_references(
                    profile_id=profile_id,
                    employee_id=employee_id,
                )
        finally:
            if owned_engine is not None:
                await owned_engine.dispose()


def _parse_employee_markdown(
    *,
    profile_id: str,
    employee_id: str,
    content: str,
) -> EmployeeMetadata:
    metadata = parse_frontmatter(content)
    raw_id = _required_string(metadata, "id")
    if raw_id != employee_id:
        raise EmployeeServiceError(
            error_code="employee_id_mismatch",
            reason=f"Employee id {raw_id} does not match file id {employee_id}.",
        )
    status = _required_string(metadata, "status")
    if status not in _VALID_STATUSES:
        raise EmployeeServiceError(
            error_code="invalid_employee_status",
            reason=f"Invalid employee status: {status}",
        )
    max_active_tasks = _positive_int(metadata.get("max_active_tasks", "1"), "max_active_tasks")
    if max_active_tasks != 1:
        raise EmployeeServiceError(
            error_code="unsupported_employee_capacity",
            reason="Employee max_active_tasks currently supports only 1.",
        )
    _, body = split_frontmatter(content)
    return EmployeeMetadata(
        id=employee_id,
        profile_id=profile_id,
        name=_required_string(metadata, "name"),
        title=_required_string(metadata, "title"),
        role=_required_string(metadata, "role"),
        status=cast(EmployeeStatus, status),
        manager_id=_optional_employee_id(metadata, "manager_id"),
        reports=_employee_id_tuple(metadata, "reports"),
        can_delegate_to=_employee_id_tuple(metadata, "can_delegate_to"),
        allowed_tools=_string_tuple(metadata, "allowed_tools"),
        can_use_subagents=_bool_value(metadata.get("can_use_subagents", False)),
        subagent_allowlist=_subagent_name_tuple(metadata, "subagent_allowlist"),
        max_active_tasks=max_active_tasks,
        body=body.strip(),
    )


def _validate_employee_id(employee_id: str) -> str:
    return validate_employee_id(employee_id)


def _required_string(metadata: dict[str, FrontmatterValue], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EmployeeServiceError(
            error_code="employee_descriptor_invalid",
            reason=f"Employee descriptor requires {key}.",
        )
    return value.strip()


def _optional_employee_id(metadata: dict[str, FrontmatterValue], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EmployeeServiceError(
            error_code="employee_descriptor_invalid",
            reason=f"Employee descriptor field {key} must be a string.",
        )
    return _validate_employee_id(value.strip())


def _employee_id_tuple(metadata: dict[str, FrontmatterValue], key: str) -> tuple[str, ...]:
    return tuple(_validate_employee_id(item) for item in _string_tuple(metadata, key))


def _subagent_name_tuple(metadata: dict[str, FrontmatterValue], key: str) -> tuple[str, ...]:
    names = _string_tuple(metadata, key)
    normalized_names: list[str] = []
    seen: set[str] = set()
    for name in names:
        if any(separator in name for separator in ("/", "\\")):
            raise EmployeeServiceError(
                error_code="employee_descriptor_invalid",
                reason=f"Employee descriptor field {key} contains an invalid subagent name.",
            )
        try:
            normalized_name = normalize_runtime_name(name)
        except ValueError as exc:
            raise EmployeeServiceError(
                error_code="employee_descriptor_invalid",
                reason=f"Employee descriptor field {key} contains an invalid subagent name.",
            ) from exc
        if normalized_name in seen:
            continue
        seen.add(normalized_name)
        normalized_names.append(normalized_name)
    return tuple(normalized_names)


def _string_tuple(metadata: dict[str, FrontmatterValue], key: str) -> tuple[str, ...]:
    value = metadata.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        items = tuple(item.strip() for item in value.split(",") if item.strip())
    elif isinstance(value, list):
        items = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        raise EmployeeServiceError(
            error_code="employee_descriptor_invalid",
            reason=f"Employee descriptor field {key} must be a string or list.",
        )
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _bool_value(value: FrontmatterValue | object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    raise EmployeeServiceError(
        error_code="employee_descriptor_invalid",
        reason="Employee descriptor field can_use_subagents must be a boolean.",
    )


def _positive_int(value: FrontmatterValue | object, field_name: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise EmployeeServiceError(
            error_code="employee_descriptor_invalid",
            reason=f"Employee descriptor field {field_name} must be a positive integer.",
        ) from exc
    if parsed < 1:
        raise EmployeeServiceError(
            error_code="employee_descriptor_invalid",
            reason=f"Employee descriptor field {field_name} must be a positive integer.",
        )
    return parsed


def _ensure_trailing_newline(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"
