"""WebSocket progress handlers for chat API routes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import time

from fastapi import Query, WebSocket, WebSocketDisconnect

from afkbot.api.chat_auth import (
    build_ws_auth_context_or_error,
    build_ws_scope_error,
    revalidate_ws_active_scope_context_or_error,
    ws_close_code_for_error,
    ws_close_reason,
)
from afkbot.api.chat_targeting import build_ws_chat_target_or_error
from afkbot.services.agent_loop.api_runtime import poll_chat_progress
from afkbot.services.agent_loop.progress_stream import ProgressCursor
from afkbot.settings import get_settings


async def ws_chat_progress(
    websocket: WebSocket,
    profile_id: str | None = Query(default=None, min_length=1),
    session_id: str | None = Query(default=None, min_length=1),
    resolve_binding: bool = Query(default=False),
    require_binding_match: bool = Query(default=False),
    transport: str | None = Query(default=None, min_length=1),
    account_id: str | None = Query(default=None, min_length=1),
    peer_id: str | None = Query(default=None, min_length=1),
    thread_id: str | None = Query(default=None, min_length=1),
    user_id: str | None = Query(default=None, min_length=1),
    run_id: int | None = Query(default=None, ge=1),
    after_event_id: int = Query(default=0, ge=0),
    poll_interval_ms: int = Query(default=150, ge=10, le=5000),
) -> None:
    """Stream progress events through WebSocket polling adapter."""

    authorization = websocket.headers.get("authorization")
    session_proof = websocket.headers.get("x-afk-session-proof")

    auth_context, auth_error = await build_ws_auth_context_or_error(
        authorization=authorization,
        session_proof=session_proof,
    )
    if auth_error is not None:
        await _close_with_error(websocket, auth_error)
        return
    assert auth_context is not None

    target, target_error = await build_ws_chat_target_or_error(
        settings=get_settings(),
        profile_id=profile_id,
        session_id=session_id,
        resolve_binding=resolve_binding,
        require_binding_match=require_binding_match,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
        default_profile_id=auth_context.profile_id,
        default_session_id=auth_context.session_id,
    )
    if target_error is not None:
        await websocket.accept()
        await websocket.send_json(target_error)
        await websocket.close(code=4400, reason=ws_close_reason(target_error))
        return
    assert target is not None

    scope_error = build_ws_scope_error(
        context=auth_context,
        profile_id=target.profile_id,
        session_id=target.session_id,
    )
    if scope_error is not None:
        await _close_with_error(websocket, scope_error)
        return

    await websocket.accept()
    cursor = ProgressCursor(run_id=run_id, last_event_id=after_event_id)
    follow_latest_run = run_id is None
    poll_interval_sec = max(0.01, poll_interval_ms / 1000)
    next_auth_check_at = _schedule_ws_auth_revalidate_at(
        auth_context=auth_context,
        poll_interval_sec=poll_interval_sec,
        now_monotonic=time.monotonic(),
    )
    try:
        while True:
            if time.monotonic() >= next_auth_check_at:
                auth_context, active_scope_error = await revalidate_ws_active_scope_context_or_error(
                    authorization=authorization,
                    session_proof=session_proof,
                    profile_id=target.profile_id,
                    session_id=target.session_id,
                )
                if active_scope_error is not None:
                    await websocket.send_json(active_scope_error)
                    await websocket.close(
                        code=ws_close_code_for_error(active_scope_error),
                        reason=ws_close_reason(active_scope_error),
                    )
                    return
                assert auth_context is not None
                next_auth_check_at = _schedule_ws_auth_revalidate_at(
                    auth_context=auth_context,
                    poll_interval_sec=poll_interval_sec,
                    now_monotonic=time.monotonic(),
                )
            poll_cursor = cursor
            if follow_latest_run:
                # Keep monotonic event cursor, but resolve latest run every poll.
                poll_cursor = ProgressCursor(run_id=None, last_event_id=cursor.last_event_id)
            payload = await poll_chat_progress(
                profile_id=target.profile_id,
                session_id=target.session_id,
                cursor=poll_cursor,
            )
            cursor = payload.cursor
            await websocket.send_json(payload.model_dump(mode="json"))
            await asyncio.sleep(poll_interval_sec)
    except WebSocketDisconnect:
        return


async def _close_with_error(websocket: WebSocket, auth_error: dict[str, object]) -> None:
    """Accept, emit, and close the socket with deterministic auth payload details."""

    await websocket.accept()
    await websocket.send_json(auth_error)
    await websocket.close(
        code=ws_close_code_for_error(auth_error),
        reason=ws_close_reason(auth_error),
    )


def _schedule_ws_auth_revalidate_at(
    *,
    auth_context: object,
    poll_interval_sec: float,
    now_monotonic: float,
) -> float:
    """Return next monotonic deadline for one WS auth/session revalidation."""

    min_interval_sec = 1.0
    interval_sec = max(min_interval_sec, min(5.0, poll_interval_sec * 10.0))
    expires_at = getattr(auth_context, "expires_at", None)
    if isinstance(expires_at, datetime):
        seconds_until_expiry = max(0.0, (expires_at - datetime.now(UTC)).total_seconds())
        interval_sec = max(min_interval_sec, min(interval_sec, seconds_until_expiry))
    return now_monotonic + interval_sec


__all__ = ["_schedule_ws_auth_revalidate_at", "ws_chat_progress"]
