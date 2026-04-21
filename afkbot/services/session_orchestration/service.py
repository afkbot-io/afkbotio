"""Canonical session-level turn orchestrator."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressCursor, ProgressEvent, ProgressStream
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.session_orchestration.contracts import SessionTurnSource
from afkbot.services.tools.base import ToolCall
from afkbot.settings import Settings, get_settings

_SESSION_QUEUE_POLL_SEC = 0.05
_SESSION_QUEUE_MAX_POLL_SEC = 0.5
_SESSION_QUEUE_HEARTBEAT_SEC = 15.0
_SESSION_QUEUE_WAIT_TIMEOUT_SEC = 86_400.0
_SESSION_QUEUE_RELEASE_RETRY_ATTEMPTS = 6
_SESSION_QUEUE_RELEASE_RETRY_INITIAL_DELAY_SEC = 0.05


@dataclass(frozen=True, slots=True)
class _SessionResources:
    session_factory: async_sessionmaker[AsyncSession]
    owned_engine: AsyncEngine | None = None


@dataclass(frozen=True, slots=True)
class SessionTurnLease:
    """Exclusive session slot that can execute multiple AgentLoop turns in sequence."""

    orchestrator: SessionOrchestrator
    session_factory: async_sessionmaker[AsyncSession]
    profile_id: str
    session_id: str
    _run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    async def run_turn(
        self,
        *,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
        progress_sink: Callable[[ProgressEvent], None] | None = None,
    ) -> TurnResult:
        async with self._run_lock:
            return await self.orchestrator._execute_locked_turn(
                session_factory=self.session_factory,
                profile_id=self.profile_id,
                session_id=self.session_id,
                message=message,
                planned_tool_calls=planned_tool_calls,
                context_overrides=context_overrides,
                progress_sink=progress_sink,
            )


class _AgentLoopRunner(Protocol):
    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult: ...


TurnRunnerFactory = Callable[[AsyncSession, str], _AgentLoopRunner]


class SessionOrchestrator:
    """Serialize turn execution for one chat session before invoking AgentLoop.

    The queue is a metadata-only durable marker queue. It provides one active writer per
    `(profile_id, session_id)` without persisting another copy of raw user messages.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        turn_runner_factory: TurnRunnerFactory | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._turn_runner_factory = turn_runner_factory or self._build_agent_loop_runner

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
        progress_sink: Callable[[ProgressEvent], None] | None = None,
        client_msg_id: str | None = None,
        source: SessionTurnSource = "chat",
    ) -> TurnResult:
        """Queue and execute one AgentLoop turn for the target session."""

        async with self.open_turn_lease(
            profile_id=profile_id,
            session_id=session_id,
            source=source,
            client_msg_id=client_msg_id,
        ) as lease:
            return await lease.run_turn(
                message=message,
                planned_tool_calls=planned_tool_calls,
                context_overrides=context_overrides,
                progress_sink=progress_sink,
            )

    async def cancel_active_turn(self, *, profile_id: str, session_id: str) -> bool:
        """Request cancellation for the latest running turn in storage."""

        resources = await _resolve_session_resources(
            shared_session_factory=self._session_factory,
            settings=self._settings,
        )
        try:
            async with session_scope(resources.session_factory) as db:
                return await RunRepository(db).request_cancel(
                    profile_id=profile_id,
                    session_id=session_id,
                )
        finally:
            await _dispose_owned_engine(resources)

    @asynccontextmanager
    async def open_turn_lease(
        self,
        *,
        profile_id: str,
        session_id: str,
        source: SessionTurnSource = "chat",
        client_msg_id: str | None = None,
    ) -> AsyncIterator[SessionTurnLease]:
        """Acquire one exclusive session slot that can run multiple turns sequentially."""

        resources = await _resolve_session_resources(
            shared_session_factory=self._session_factory,
            settings=self._settings,
        )
        owner_token = uuid.uuid4().hex
        normalized_client_msg_id = str(client_msg_id or "").strip() or None
        try:
            queue_item_id = await self._enqueue_turn_marker(
                session_factory=resources.session_factory,
                profile_id=profile_id,
                session_id=session_id,
                owner_token=owner_token,
                source=source,
                client_msg_id=normalized_client_msg_id,
            )
            heartbeat_task = asyncio.create_task(
                self._heartbeat_queue_marker(
                    session_factory=resources.session_factory,
                    queue_item_id=queue_item_id,
                    owner_token=owner_token,
                ),
                name=f"session-turn-queue-heartbeat:{profile_id}:{session_id}:{queue_item_id}",
            )
            try:
                await self._wait_until_marker_is_running(
                    session_factory=resources.session_factory,
                    queue_item_id=queue_item_id,
                    profile_id=profile_id,
                    session_id=session_id,
                    owner_token=owner_token,
                )
                yield SessionTurnLease(
                    orchestrator=self,
                    session_factory=resources.session_factory,
                    profile_id=profile_id,
                    session_id=session_id,
                )
            finally:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
                await self._release_queue_marker(
                    session_factory=resources.session_factory,
                    queue_item_id=queue_item_id,
                    owner_token=owner_token,
                )
        finally:
            await _dispose_owned_engine(resources)

    async def _enqueue_turn_marker(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        profile_id: str,
        session_id: str,
        owner_token: str,
        source: str,
        client_msg_id: str | None,
    ) -> int:
        async with session_scope(session_factory) as db:
            await ProfileRepository(db).get_or_create_default(profile_id)
            row = await ChatSessionTurnQueueRepository(db).enqueue(
                profile_id=profile_id,
                session_id=session_id,
                owner_token=owner_token,
                source=source,
                client_msg_id=client_msg_id,
            )
            return row.id

    async def _wait_until_marker_is_running(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue_item_id: int,
        profile_id: str,
        session_id: str,
        owner_token: str,
    ) -> None:
        deadline = time.monotonic() + _SESSION_QUEUE_WAIT_TIMEOUT_SEC
        attempt = 0
        while time.monotonic() < deadline:
            async with session_scope(session_factory) as db:
                repo = ChatSessionTurnQueueRepository(db)
                await repo.release_stale(
                    profile_id=profile_id,
                    session_id=session_id,
                    older_than=self._stale_queue_cutoff(),
                )
                if await repo.try_mark_running(
                    queue_item_id=queue_item_id,
                    profile_id=profile_id,
                    session_id=session_id,
                    owner_token=owner_token,
                    touched_at=datetime.now(UTC),
                ):
                    return
            await asyncio.sleep(_session_queue_poll_delay(attempt))
            attempt += 1
        raise TimeoutError(
            f"Timed out waiting for session turn queue slot: {profile_id}/{session_id}"
        )

    async def _heartbeat_queue_marker(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue_item_id: int,
        owner_token: str,
    ) -> None:
        while True:
            async with session_scope(session_factory) as db:
                touched = await ChatSessionTurnQueueRepository(db).touch(
                    queue_item_id=queue_item_id,
                    owner_token=owner_token,
                    touched_at=datetime.now(UTC),
                )
            if not touched:
                return
            await asyncio.sleep(_SESSION_QUEUE_HEARTBEAT_SEC)

    async def _release_queue_marker(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue_item_id: int,
        owner_token: str,
    ) -> None:
        delay_sec = _SESSION_QUEUE_RELEASE_RETRY_INITIAL_DELAY_SEC
        for attempt in range(_SESSION_QUEUE_RELEASE_RETRY_ATTEMPTS):
            try:
                async with session_scope(session_factory) as db:
                    await ChatSessionTurnQueueRepository(db).release(
                        queue_item_id=queue_item_id,
                        owner_token=owner_token,
                    )
                return
            except OperationalError as exc:
                if attempt + 1 >= _SESSION_QUEUE_RELEASE_RETRY_ATTEMPTS or not _is_sqlite_lock_error(
                    exc
                ):
                    raise
                await asyncio.sleep(delay_sec)
                delay_sec = min(delay_sec * 2, 0.25)

    async def _execute_locked_turn(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        profile_id: str,
        session_id: str,
        message: str,
        planned_tool_calls: list[ToolCall] | None,
        context_overrides: TurnContextOverrides | None,
        progress_sink: Callable[[ProgressEvent], None] | None,
    ) -> TurnResult:
        async with session_scope(session_factory) as db:
            previous_latest_run_id = await RunRepository(db).get_latest_run_id(
                profile_id=profile_id,
                session_id=session_id,
            )
            runner = self._turn_runner_factory(db, profile_id)
            task = asyncio.create_task(
                runner.run_turn(
                    profile_id=profile_id,
                    session_id=session_id,
                    message=message,
                    planned_tool_calls=planned_tool_calls,
                    context_overrides=context_overrides,
                ),
                name=f"session-turn:{profile_id}:{session_id}",
            )
            try:
                if progress_sink is None:
                    return await task
                return await self._await_turn_with_progress(
                    task=task,
                    session_factory=session_factory,
                    profile_id=profile_id,
                    session_id=session_id,
                    previous_latest_run_id=previous_latest_run_id,
                    progress_sink=progress_sink,
                )
            except asyncio.CancelledError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise

    async def _await_turn_with_progress(
        self,
        *,
        task: asyncio.Task[TurnResult],
        session_factory: async_sessionmaker[AsyncSession],
        profile_id: str,
        session_id: str,
        previous_latest_run_id: int | None,
        progress_sink: Callable[[ProgressEvent], None],
    ) -> TurnResult:
        cursor = ProgressCursor()
        emitted_event_ids: set[int] = set()
        poll_interval_sec = max(0.01, self._settings.cli_progress_poll_interval_ms / 1000)

        while not task.done():
            cursor = await self._pin_cursor_to_current_run(
                session_factory=session_factory,
                cursor=cursor,
                profile_id=profile_id,
                session_id=session_id,
                previous_latest_run_id=previous_latest_run_id,
            )
            cursor, _ = await self._poll_and_emit_progress(
                session_factory=session_factory,
                cursor=cursor,
                profile_id=profile_id,
                session_id=session_id,
                emitted_event_ids=emitted_event_ids,
                progress_sink=progress_sink,
            )
            await asyncio.sleep(poll_interval_sec)

        empty_polls_after_done = 0
        while empty_polls_after_done < 3:
            cursor = await self._pin_cursor_to_current_run(
                session_factory=session_factory,
                cursor=cursor,
                profile_id=profile_id,
                session_id=session_id,
                previous_latest_run_id=previous_latest_run_id,
            )
            cursor, emitted = await self._poll_and_emit_progress(
                session_factory=session_factory,
                cursor=cursor,
                profile_id=profile_id,
                session_id=session_id,
                emitted_event_ids=emitted_event_ids,
                progress_sink=progress_sink,
            )
            if emitted == 0:
                empty_polls_after_done += 1
                await asyncio.sleep(poll_interval_sec)
                continue
            empty_polls_after_done = 0

        return await task

    async def _pin_cursor_to_current_run(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cursor: ProgressCursor,
        profile_id: str,
        session_id: str,
        previous_latest_run_id: int | None,
    ) -> ProgressCursor:
        if cursor.run_id is not None:
            return cursor
        async with session_scope(session_factory) as poll_session:
            stream = ProgressStream(poll_session)
            latest_run_id = await stream.resolve_latest_run_id(
                profile_id=profile_id,
                session_id=session_id,
            )
        if latest_run_id is None:
            return cursor
        if previous_latest_run_id is not None and latest_run_id <= previous_latest_run_id:
            return cursor
        return ProgressCursor(run_id=latest_run_id, last_event_id=cursor.last_event_id)

    async def _poll_and_emit_progress(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cursor: ProgressCursor,
        profile_id: str,
        session_id: str,
        emitted_event_ids: set[int],
        progress_sink: Callable[[ProgressEvent], None],
    ) -> tuple[ProgressCursor, int]:
        if cursor.run_id is None:
            return cursor, 0

        async with session_scope(session_factory) as poll_session:
            stream = ProgressStream(
                poll_session,
                batch_size=self._settings.cli_progress_batch_size,
            )
            events, next_cursor = await stream.poll(
                profile_id=profile_id,
                session_id=session_id,
                cursor=cursor,
            )
        emitted = 0
        for event in events:
            if event.event_id in emitted_event_ids:
                continue
            emitted_event_ids.add(event.event_id)
            progress_sink(event)
            emitted += 1
        return next_cursor, emitted

    def _stale_queue_cutoff(self) -> datetime:
        return session_turn_queue_stale_cutoff(settings=self._settings)

    def _build_agent_loop_runner(self, session: AsyncSession, profile_id: str) -> _AgentLoopRunner:
        from afkbot.services.agent_loop.runtime_factory import build_profile_agent_loop

        return build_profile_agent_loop(
            session,
            settings=self._settings,
            profile_id=profile_id,
        )


async def _resolve_session_resources(
    *,
    shared_session_factory: async_sessionmaker[AsyncSession] | None,
    settings: Settings,
) -> _SessionResources:
    if shared_session_factory is not None:
        return _SessionResources(session_factory=shared_session_factory)

    from afkbot.db.bootstrap import create_schema
    from afkbot.db.engine import create_engine
    from afkbot.db.session import create_session_factory

    owned_engine = create_engine(settings)
    await create_schema(owned_engine)
    return _SessionResources(
        session_factory=create_session_factory(owned_engine),
        owned_engine=owned_engine,
    )


async def _dispose_owned_engine(resources: _SessionResources) -> None:
    if resources.owned_engine is not None:
        await resources.owned_engine.dispose()


def session_turn_queue_stale_cutoff(
    *,
    settings: Settings,
    now: datetime | None = None,
) -> datetime:
    """Return the cutoff after which queue markers are considered stale."""

    reference = now or datetime.now(UTC)
    ttl_sec = max(
        300.0,
        float(settings.tool_timeout_max_sec * 3),
        float(settings.llm_request_timeout_sec * 2),
    )
    return reference - timedelta(seconds=ttl_sec)


def _session_queue_poll_delay(attempt: int) -> float:
    """Return one capped backoff interval for queue admission polling."""

    if attempt <= 0:
        return _SESSION_QUEUE_POLL_SEC
    return float(min(_SESSION_QUEUE_MAX_POLL_SEC, _SESSION_QUEUE_POLL_SEC * (2**attempt)))


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()
