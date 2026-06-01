"""Markdown storage for profile-scoped Task Flow employees."""

from __future__ import annotations

import asyncio
from pathlib import Path

from afkbot.services.atomic_writes import atomic_text_write
from afkbot.services.employees.errors import EmployeeServiceError
from afkbot.services.employees.ids import validate_employee_id
from afkbot.services.path_scope import resolve_in_scope_or_none
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.settings import Settings


class EmployeeMarkdownStore:
    """Read and write employee markdown files under one profile root."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def employees_root(self, profile_id: str) -> Path:
        """Return the safe employee directory for one profile."""

        normalized_profile_id = _validate_profile(profile_id)
        profiles_root = self._settings.profiles_dir.resolve()
        root = (profiles_root / normalized_profile_id / "employees").resolve()
        if not root.is_relative_to(profiles_root):
            raise EmployeeServiceError(
                error_code="invalid_profile_id",
                reason=f"Invalid profile id: {profile_id}",
            )
        return root

    def employee_path(self, *, profile_id: str, employee_id: str) -> Path:
        """Return the safe markdown path for one employee."""

        root = self.employees_root(profile_id)
        normalized_employee_id = _validate_employee_id(employee_id)
        try:
            path = root / f"{normalized_employee_id}.md"
        except (OSError, RuntimeError) as exc:
            raise EmployeeServiceError(
                error_code="invalid_employee_id",
                reason=f"Invalid employee id: {employee_id}",
            ) from exc
        if path.parent != root:
            raise EmployeeServiceError(
                error_code="invalid_employee_id",
                reason=f"Invalid employee id: {employee_id}",
            )
        return path

    async def list_markdown(self, profile_id: str) -> list[tuple[str, str]]:
        """Return safe employee markdown entries sorted by employee id."""

        root = self.employees_root(profile_id)
        entries = await asyncio.to_thread(self._list_markdown_sync, root)
        return [(path.stem, await asyncio.to_thread(path.read_text, encoding="utf-8")) for path in entries]

    async def get_markdown(self, *, profile_id: str, employee_id: str) -> str:
        """Return one employee markdown file."""

        path = self.employee_path(profile_id=profile_id, employee_id=employee_id)
        safe_path = await asyncio.to_thread(
            resolve_in_scope_or_none,
            path,
            scope_root=self.employees_root(profile_id),
            strict=True,
        )
        if safe_path is None or not safe_path.is_file():
            raise EmployeeServiceError(
                error_code="employee_not_found",
                reason=f"Employee not found: {employee_id}",
            )
        return await asyncio.to_thread(safe_path.read_text, encoding="utf-8")

    async def write_markdown(
        self,
        *,
        profile_id: str,
        employee_id: str,
        content: str,
    ) -> Path:
        """Atomically write one employee markdown file."""

        path = self.employee_path(profile_id=profile_id, employee_id=employee_id)
        await asyncio.to_thread(atomic_text_write, path, content, mode=0o600)
        return path

    async def delete_markdown(self, *, profile_id: str, employee_id: str) -> str:
        """Delete one employee markdown file and return its prior content."""

        path = self.employee_path(profile_id=profile_id, employee_id=employee_id)
        safe_path = await asyncio.to_thread(
            resolve_in_scope_or_none,
            path,
            scope_root=self.employees_root(profile_id),
            strict=True,
        )
        if safe_path is None or not safe_path.is_file():
            raise EmployeeServiceError(
                error_code="employee_not_found",
                reason=f"Employee not found: {employee_id}",
            )
        content = await asyncio.to_thread(safe_path.read_text, encoding="utf-8")
        await asyncio.to_thread(safe_path.unlink)
        return content

    @staticmethod
    def _list_markdown_sync(root: Path) -> list[Path]:
        if not root.exists():
            return []
        root_resolved = root.resolve()
        result: list[Path] = []
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix != ".md":
                continue
            safe_path = resolve_in_scope_or_none(path, scope_root=root_resolved, strict=True)
            if safe_path is None or not safe_path.is_file():
                continue
            result.append(safe_path)
        return result


def _validate_profile(profile_id: str) -> str:
    try:
        return validate_profile_id(str(profile_id or "").strip())
    except InvalidProfileIdError as exc:
        raise EmployeeServiceError(
            error_code="invalid_profile_id",
            reason=str(exc),
        ) from exc


def _validate_employee_id(employee_id: str) -> str:
    return validate_employee_id(employee_id)
