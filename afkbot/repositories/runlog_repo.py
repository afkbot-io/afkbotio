"""Repository for runlog events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.run import Run
from afkbot.models.runlog_event import RunlogEvent


@dataclass(frozen=True, slots=True)
class RunlogEventRead:
    """Read model for persisted runlog event."""

    id: int
    run_id: int
    session_id: str
    event_type: str
    payload_json: str
    created_at: datetime


class RunlogRepository:
    """Persistence operations for runlog events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_event(
        self,
        run_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunlogEvent:
        """Create runlog event."""

        event = RunlogEvent(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str),
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_run_events_since(
        self,
        run_id: int,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        """List run events in ascending id order after provided event id."""

        if limit <= 0:
            return []

        statement: Select[tuple[RunlogEvent]] = (
            select(RunlogEvent)
            .where(
                RunlogEvent.run_id == run_id,
                RunlogEvent.id > after_event_id,
            )
            .order_by(RunlogEvent.id.asc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [
            RunlogEventRead(
                id=row.id,
                run_id=row.run_id,
                session_id=row.session_id,
                event_type=row.event_type,
                payload_json=row.payload_json,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def list_session_events_by_type(
        self,
        *,
        profile_id: str,
        session_id: str,
        event_type: str,
        limit: int = 50,
    ) -> list[RunlogEventRead]:
        """List recent events for one profile/session filtered by event type."""

        if limit <= 0:
            return []

        statement: Select[tuple[RunlogEvent]] = (
            select(RunlogEvent)
            .join(Run, Run.id == RunlogEvent.run_id)
            .where(
                Run.profile_id == profile_id,
                RunlogEvent.session_id == session_id,
                RunlogEvent.event_type == event_type,
            )
            .order_by(RunlogEvent.id.desc())
            .limit(limit)
        )

        rows = (await self._session.execute(statement)).scalars().all()
        return [
            RunlogEventRead(
                id=row.id,
                run_id=row.run_id,
                session_id=row.session_id,
                event_type=row.event_type,
                payload_json=row.payload_json,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def list_session_events(
        self,
        *,
        session_id: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RunlogEventRead]:
        """List most recent events for one chat session in reverse chronological order."""

        if limit <= 0:
            return []

        statement: Select[tuple[RunlogEvent]] = select(RunlogEvent).where(
            RunlogEvent.session_id == session_id
        )
        if event_type is not None:
            statement = statement.where(RunlogEvent.event_type == event_type)
        statement = statement.order_by(RunlogEvent.id.desc()).limit(limit)
        rows = (await self._session.execute(statement)).scalars().all()
        return [
            RunlogEventRead(
                id=row.id,
                run_id=row.run_id,
                session_id=row.session_id,
                event_type=row.event_type,
                payload_json=row.payload_json,
                created_at=row.created_at,
            )
            for row in rows
        ]
