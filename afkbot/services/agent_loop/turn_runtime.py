"""Runtime helpers for one-turn execution, secure input replay, and progress streaming."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressCursor, ProgressEvent, ProgressStream
from afkbot.services.agent_loop.runtime_factory import build_profile_agent_loop
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.tools.base import ToolCall
from afkbot.settings import get_settings

ProgressSink = Callable[[ProgressEvent], None]


async def run_once_result(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    planned_tool_calls: list[ToolCall] | None = None,
    progress_sink: ProgressSink | None = None,
    context_overrides: TurnContextOverrides | None = None,
) -> TurnResult:
    """Run one loop turn and return typed result payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    await create_schema(engine)
    try:
        async with session_scope(session_factory) as db:
            loop = build_profile_agent_loop(
                db,
                settings=settings,
                profile_id=profile_id,
            )
            previous_latest_run_id = await RunRepository(db).get_latest_run_id(
                profile_id=profile_id,
                session_id=session_id,
            )
            _, task = await loop.start_tracked_turn(
                profile_id=profile_id,
                session_id=session_id,
                message=message,
                planned_tool_calls=planned_tool_calls,
                context_overrides=context_overrides,
            )
            if progress_sink is None:
                return await task
            return await _await_turn_with_progress(
                task=task,
                session_factory=session_factory,
                profile_id=profile_id,
                session_id=session_id,
                previous_latest_run_id=previous_latest_run_id,
                poll_interval_ms=settings.cli_progress_poll_interval_ms,
                batch_size=settings.cli_progress_batch_size,
                progress_sink=progress_sink,
            )
    finally:
        await engine.dispose()


async def submit_secure_field(
    *,
    profile_id: str,
    envelope: ActionEnvelope,
    secret_value: str,
    session_id: str | None = None,
) -> tuple[bool, str]:
    """Persist one secure credential value requested by `request_secure_field` envelope."""

    patch = envelope.spec_patch or {}
    question_id = (envelope.question_id or "").strip()
    secure_field = (envelope.secure_field or "").strip()
    secure_nonce = str(patch.get("secure_nonce") or "").strip()
    resolved_session_id = (session_id or "").strip()
    if not resolved_session_id:
        return False, "session_id_missing"
    if not question_id:
        return False, "question_id_missing"
    if not secure_field:
        return False, "credential_name_missing"
    if not secure_nonce:
        return False, "secure_nonce_missing"

    if secret_value == "":
        return False, "secret_value_empty"

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    pending_id: int | None = None
    try:
        async with session_scope(session_factory) as db:
            pending_repo = PendingSecureRequestRepository(db)
            pending = await pending_repo.claim_active(
                profile_id=profile_id,
                session_id=resolved_session_id,
                question_id=question_id,
                secure_field=secure_field,
                nonce=secure_nonce,
            )
            if pending is None:
                return False, "secure_request_invalid_or_expired"
            pending_id = pending.id
            integration_name = pending.integration_name
            credential_name = pending.credential_name
            credential_profile_key = pending.credential_profile_key
            tool_name = pending.tool_name or None
            if tool_name == "credentials.request":
                tool_name = "app.run"

        service = get_credentials_service(settings)
        await service.create(
            profile_id=profile_id,
            tool_name=tool_name,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
            replace_existing=True,
        )
        return True, "ok"
    except CredentialsServiceError as exc:
        if pending_id is not None:
            async with session_scope(session_factory) as db:
                await PendingSecureRequestRepository(db).release_claim(row_id=pending_id)
        return False, exc.error_code
    except Exception:
        if pending_id is not None:
            async with session_scope(session_factory) as db:
                await PendingSecureRequestRepository(db).release_claim(row_id=pending_id)
        raise
    finally:
        await engine.dispose()


async def _await_turn_with_progress(
    *,
    task: asyncio.Task[TurnResult],
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    session_id: str,
    previous_latest_run_id: int | None,
    poll_interval_ms: int,
    batch_size: int,
    progress_sink: ProgressSink,
) -> TurnResult:
    """Await turn task while polling progress events from storage."""

    cursor = ProgressCursor()
    emitted_event_ids: set[int] = set()
    poll_interval_sec = max(0.01, poll_interval_ms / 1000)

    while not task.done():
        cursor = await _pin_cursor_to_current_run(
            session_factory=session_factory,
            cursor=cursor,
            profile_id=profile_id,
            session_id=session_id,
            previous_latest_run_id=previous_latest_run_id,
        )
        cursor, _ = await _poll_and_emit_progress(
            session_factory=session_factory,
            cursor=cursor,
            profile_id=profile_id,
            session_id=session_id,
            batch_size=batch_size,
            emitted_event_ids=emitted_event_ids,
            progress_sink=progress_sink,
        )
        await asyncio.sleep(poll_interval_sec)

    empty_polls_after_done = 0
    while empty_polls_after_done < 3:
        cursor = await _pin_cursor_to_current_run(
            session_factory=session_factory,
            cursor=cursor,
            profile_id=profile_id,
            session_id=session_id,
            previous_latest_run_id=previous_latest_run_id,
        )
        cursor, emitted = await _poll_and_emit_progress(
            session_factory=session_factory,
            cursor=cursor,
            profile_id=profile_id,
            session_id=session_id,
            batch_size=batch_size,
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
    *,
    session_factory: async_sessionmaker[AsyncSession],
    cursor: ProgressCursor,
    profile_id: str,
    session_id: str,
    previous_latest_run_id: int | None,
) -> ProgressCursor:
    """Bind progress cursor to the run started by current command invocation."""

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
    *,
    session_factory: async_sessionmaker[AsyncSession],
    cursor: ProgressCursor,
    profile_id: str,
    session_id: str,
    batch_size: int,
    emitted_event_ids: set[int],
    progress_sink: ProgressSink,
) -> tuple[ProgressCursor, int]:
    """Poll progress stream and emit deduplicated events."""

    if cursor.run_id is None:
        return cursor, 0

    async with session_scope(session_factory) as poll_session:
        stream = ProgressStream(poll_session, batch_size=batch_size)
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
