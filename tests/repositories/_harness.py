"""Shared database/bootstrap helpers for repository tests."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.settings import Settings

_DEFAULT_EMPLOYEE_IDS = (
    "default",
    "analyst",
    "papercliper",
    "outsider",
    "auditor",
    "researcher",
    "reviewer",
)


async def build_repository_factory(
    tmp_path: Path,
    *,
    db_name: str,
    profile_ids: tuple[str, ...] = ("default",),
    session_specs: tuple[tuple[str, str], ...] = (),
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create a temporary repository database with optional profiles and sessions."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        taskflow_public_principal_required=False,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    setattr(factory, "_afkbot_test_settings", settings)
    for profile_id in profile_ids:
        _write_test_employees(settings=settings, profile_id=profile_id)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        for profile_id in profile_ids:
            await profiles.get_or_create_default(profile_id)
        if session_specs:
            sessions = ChatSessionRepository(session)
            for session_id, profile_id in session_specs:
                await sessions.create(session_id=session_id, profile_id=profile_id)
    return engine, factory


def _write_test_employees(*, settings: Settings, profile_id: str) -> None:
    """Seed reusable employees for Task Flow service tests."""

    employees_dir = settings.profiles_dir / profile_id / "employees"
    employees_dir.mkdir(parents=True, exist_ok=True)
    employee_ids = (*_DEFAULT_EMPLOYEE_IDS, profile_id)
    for employee_id in dict.fromkeys(employee_ids):
        if employee_id == "default":
            manager_line = ""
        elif employee_id in {"papercliper", "researcher", "reviewer"}:
            manager_line = "manager_id: analyst\n"
        else:
            manager_line = "manager_id: default\n"
        (employees_dir / f"{employee_id}.md").write_text(
            "---\n"
            f"id: {employee_id}\n"
            f"name: {employee_id.title()}\n"
            "title: Test employee\n"
            "role: task-flow-test\n"
            "status: active\n"
            f"{manager_line}"
            "allowed_tools:\n"
            "  - task.*\n"
            "max_active_tasks: 1\n"
            "---\n\n"
            f"Test employee {employee_id} for Task Flow service coverage.\n",
            encoding="utf-8",
        )
