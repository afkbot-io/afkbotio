"""Persistence backend for subagent task lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.profile_policy import ProfilePolicy
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.subagent_task_repo import SubagentTaskRepository
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.subagents.runtime_support import (
    SubagentTaskState,
    ensure_owner_access,
    is_task_overdue,
)
from afkbot.settings import Settings

TRepoValue = TypeVar("TRepoValue")


class SubagentTaskStore:
    """Persist and load subagent task state through one cached async engine."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def shutdown(self) -> None:
        """Dispose owned database engine."""

        await self._engine.dispose()

    async def ensure_schema(self) -> None:
        """Create schema lazily once per store instance."""

        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await create_schema(self._engine)
            self._schema_ready = True

    async def ensure_profile_policy(self, *, profile_id: str) -> ProfilePolicy:
        """Ensure target profile/policy rows exist and return current policy."""

        async def _op(session: AsyncSession) -> ProfilePolicy:
            await ProfileRepository(session).get_or_create_default(profile_id)
            return await ProfilePolicyRepository(session).get_or_create_default(profile_id)

        policy = await self._with_session(_op)
        get_profile_runtime_config_service(self._settings).ensure_layout(profile_id)
        return policy

    async def create_task(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
        run_id: int,
        subagent_name: str,
        prompt: str,
        timeout_sec: int,
        created_at: datetime,
    ) -> None:
        """Persist one newly accepted subagent task."""

        async def _op(session: AsyncSession) -> None:
            await SubagentTaskRepository(session).create_task(
                task_id=task_id,
                profile_id=profile_id,
                session_id=session_id,
                run_id=run_id,
                subagent_name=subagent_name,
                prompt=prompt,
                timeout_sec=timeout_sec,
                created_at=created_at,
            )

        await self._with_session(_op)

    async def claim_task_start(
        self,
        *,
        task_id: str,
        started_at: datetime,
    ) -> SubagentTaskState | None:
        """Mark one task running and return claimed state for worker execution."""

        async def _op(session: AsyncSession) -> SubagentTaskState | None:
            row = await SubagentTaskRepository(session).claim_task_start(
                task_id=task_id,
                started_at=started_at,
            )
            if row is None:
                return None
            return SubagentTaskState.from_row(row)

        return await self._with_session(_op)

    async def load_state(
        self,
        *,
        task_id: str,
        profile_id: str | None,
        session_id: str | None,
        mark_overdue_timeout: bool,
    ) -> SubagentTaskState:
        """Load one task state, enforce ownership, and promote overdue runs to timeout."""

        async def _op(session: AsyncSession) -> SubagentTaskState:
            repo = SubagentTaskRepository(session)
            row = await repo.get_task(task_id=task_id)
            if row is None:
                raise KeyError("subagent_task_not_found")
            ensure_owner_access(
                task_profile_id=row.profile_id,
                task_session_id=row.session_id,
                profile_id=profile_id,
                session_id=session_id,
            )
            if mark_overdue_timeout and row.status == "running" and is_task_overdue(
                row,
                settings=self._settings,
            ):
                await repo.finish_task(
                    task_id=row.task_id,
                    status="timeout",
                    finished_at=datetime.now(timezone.utc),
                    child_session_id=row.child_session_id,
                    child_run_id=row.child_run_id,
                    output=None,
                    error_code="subagent_timeout",
                    reason=f"Subagent timed out after {row.timeout_sec} seconds",
                )
                row = await repo.get_task(task_id=task_id)
                if row is None:
                    raise KeyError("subagent_task_not_found")
            return SubagentTaskState.from_row(row)

        return await self._with_session(_op)

    async def finish_task(
        self,
        *,
        task_id: str,
        status: str,
        child_session_id: str | None,
        child_run_id: int | None,
        output: str | None,
        error_code: str | None,
        reason: str | None,
    ) -> None:
        """Persist terminal task state with optional child trace metadata."""

        async def _op(session: AsyncSession) -> None:
            await SubagentTaskRepository(session).finish_task(
                task_id=task_id,
                status=status,
                finished_at=datetime.now(timezone.utc),
                child_session_id=child_session_id,
                child_run_id=child_run_id,
                output=output,
                error_code=error_code,
                reason=reason,
            )

        await self._with_session(_op)

    async def prune_expired_tasks(self) -> None:
        """Delete finished tasks older than configured TTL."""

        ttl_sec = max(1, int(self._settings.subagent_task_ttl_sec))
        threshold = datetime.now(timezone.utc) - timedelta(seconds=ttl_sec)

        async def _op(session: AsyncSession) -> None:
            await SubagentTaskRepository(session).delete_finished_before(before_utc=threshold)

        await self._with_session(_op)

    async def with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TRepoValue]],
    ) -> TRepoValue:
        """Run one repository operation inside managed async session."""

        return await self._with_session(op)

    async def _with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TRepoValue]],
    ) -> TRepoValue:
        await self.ensure_schema()
        async with session_scope(self._session_factory) as session:
            return await op(session)
