"""WebSocket progress route tests for chat API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import time

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.api.routes_chat import _schedule_ws_auth_revalidate_at
from afkbot.services.agent_loop.api_runtime import ProgressPollResponse
from afkbot.services.agent_loop.progress_stream import ProgressCursor, ProgressEvent
from afkbot.services.connect import ConnectAccessTokenContext
from tests.api.chat_api._harness import auth_headers, patch_valid_chat_access_token


def test_chat_progress_ws_streams_payload(monkeypatch: MonkeyPatch) -> None:
    """WS progress route should push progress frames in JSON format."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)
    state = {"calls": 0, "seen": []}

    async def _fake_poll(
        *,
        profile_id: str,
        session_id: str,
        cursor: ProgressCursor,
    ) -> ProgressPollResponse:
        _ = profile_id, session_id
        state["calls"] += 1
        state["seen"].append({"run_id": cursor.run_id, "last_event_id": cursor.last_event_id})
        if state["calls"] == 1:
            return ProgressPollResponse(
                events=[
                    ProgressEvent(
                        event_id=1,
                        run_id=9,
                        stage="planning",
                        iteration=0,
                        tool_name=None,
                        event_type="turn.progress",
                    )
                ],
                cursor=ProgressCursor(run_id=9, last_event_id=1),
            )
        raise WebSocketDisconnect(code=1000)

    monkeypatch.setattr("afkbot.api.chat_routes.websocket.poll_chat_progress", _fake_poll)

    # Act
    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/v1/chat/progress/ws?profile_id=default&session_id=api-s&poll_interval_ms=10",
            headers=auth_headers(),
        ) as ws:
            payload = ws.receive_json()
            deadline = time.time() + 0.3
            while len(state["seen"]) < 2 and time.time() < deadline:
                time.sleep(0.01)

    # Assert
    assert payload["cursor"] == {"run_id": 9, "last_event_id": 1}
    assert payload["events"][0]["stage"] == "planning"
    assert len(state["seen"]) >= 2
    assert state["seen"][0] == {"run_id": None, "last_event_id": 0}
    assert state["seen"][1] == {"run_id": None, "last_event_id": 1}


def test_chat_progress_ws_revalidates_auth_after_accept(monkeypatch: MonkeyPatch) -> None:
    """WS progress route should close an already-open socket when auth becomes invalid."""

    # Arrange
    state = {"auth_calls": 0, "poll_calls": 0}

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        assert access_token == "acc-1"
        assert session_proof == "proof-1"
        _ = session_factory
        state["auth_calls"] += 1
        if state["auth_calls"] >= 3:
            from afkbot.services.connect import ConnectServiceError

            raise ConnectServiceError(
                error_code="connect_access_token_revoked",
                reason="revoked mid-stream",
            )
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

    async def _fake_poll(
        *,
        profile_id: str,
        session_id: str,
        cursor: ProgressCursor,
    ) -> ProgressPollResponse:
        _ = profile_id, session_id, cursor
        state["poll_calls"] += 1
        return ProgressPollResponse(
            events=[
                ProgressEvent(
                    event_id=state["poll_calls"],
                    run_id=11,
                    stage="thinking",
                    iteration=0,
                    tool_name=None,
                    event_type="turn.progress",
                )
            ],
            cursor=ProgressCursor(run_id=11, last_event_id=state["poll_calls"]),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    monkeypatch.setattr("afkbot.api.chat_routes.websocket.poll_chat_progress", _fake_poll)
    monkeypatch.setattr(
        "afkbot.api.chat_routes.websocket._schedule_ws_auth_revalidate_at",
        lambda **kwargs: kwargs["now_monotonic"],
    )
    client = TestClient(create_app())

    # Act
    with client.websocket_connect(
        "/v1/chat/progress/ws?profile_id=default&session_id=api-s&poll_interval_ms=10",
        headers=auth_headers(),
    ) as ws:
        payload = ws.receive_json()
        close_payload = ws.receive_json()
        try:
            ws.receive_json()
            raise AssertionError("expected websocket disconnect")
        except WebSocketDisconnect as exc:
            disconnect_code = exc.code

    # Assert
    assert payload["events"][0]["event_id"] == 1
    assert close_payload == {
        "ok": False,
        "error_code": "connect_access_token_revoked",
        "reason": "revoked mid-stream",
    }
    assert disconnect_code == 4401
    assert state["auth_calls"] >= 3
    assert state["poll_calls"] == 1


def test_chat_progress_ws_throttles_auth_revalidation_between_polls(
    monkeypatch: MonkeyPatch,
) -> None:
    """WS progress route should not hit token validation on every short poll iteration."""

    # Arrange
    state = {"auth_calls": 0, "poll_calls": 0}

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        assert access_token == "acc-1"
        assert session_proof == "proof-1"
        _ = session_factory
        state["auth_calls"] += 1
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )

    async def _fake_poll(
        *,
        profile_id: str,
        session_id: str,
        cursor: ProgressCursor,
    ) -> ProgressPollResponse:
        _ = profile_id, session_id
        state["poll_calls"] += 1
        if state["poll_calls"] >= 2:
            raise WebSocketDisconnect(code=1000)
        return ProgressPollResponse(
            events=[
                ProgressEvent(
                    event_id=1,
                    run_id=12,
                    stage="thinking",
                    iteration=0,
                    tool_name=None,
                    event_type="turn.progress",
                )
            ],
            cursor=ProgressCursor(run_id=12, last_event_id=max(cursor.last_event_id, 1)),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    monkeypatch.setattr("afkbot.api.chat_routes.websocket.poll_chat_progress", _fake_poll)

    # Act
    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/v1/chat/progress/ws?profile_id=default&session_id=api-s&poll_interval_ms=10",
            headers=auth_headers(),
        ) as ws:
            payload = ws.receive_json()
            deadline = time.monotonic() + 0.5
            while state["poll_calls"] < 2 and time.monotonic() < deadline:
                time.sleep(0.01)

    # Assert
    assert payload["events"][0]["event_id"] == 1
    assert state["poll_calls"] >= 2
    assert state["auth_calls"] == 1


def test_schedule_ws_auth_revalidate_at_keeps_one_second_floor_near_expiry() -> None:
    """WS auth revalidation should keep the one-second floor even when expiry is imminent."""

    # Arrange
    auth_context = ConnectAccessTokenContext(
        profile_id="default",
        session_id="api-s",
        base_url="http://127.0.0.1:8081",
        expires_at=datetime.now(tz=UTC) - timedelta(seconds=5),
    )

    # Act
    deadline = _schedule_ws_auth_revalidate_at(
        auth_context=auth_context,
        poll_interval_sec=0.01,
        now_monotonic=100.0,
    )

    # Assert
    assert deadline == pytest.approx(101.0)


def test_chat_progress_ws_requires_authorization() -> None:
    """WS progress route should emit auth error payload and close when header missing."""

    # Arrange
    client = TestClient(create_app())

    # Act
    with client.websocket_connect("/v1/chat/progress/ws?profile_id=default&session_id=api-s") as ws:
        payload = ws.receive_json()
        try:
            ws.receive_json()
            raise AssertionError("expected websocket disconnect")
        except WebSocketDisconnect as exc:
            disconnect_code = exc.code

    # Assert
    assert payload == {
        "ok": False,
        "error_code": "chat_access_token_missing",
        "reason": "Authorization header with Bearer token is required.",
    }
    assert disconnect_code == 4401


def test_chat_progress_ws_rejects_scope_mismatch(monkeypatch: MonkeyPatch) -> None:
    """WS progress route should emit deterministic mismatch payload and close."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch, profile_id="other-profile")
    client = TestClient(create_app())

    # Act
    with client.websocket_connect(
        "/v1/chat/progress/ws?profile_id=default&session_id=api-s",
        headers=auth_headers(),
    ) as ws:
        payload = ws.receive_json()
        try:
            ws.receive_json()
            raise AssertionError("expected websocket disconnect")
        except WebSocketDisconnect as exc:
            disconnect_code = exc.code

    # Assert
    assert payload == {
        "ok": False,
        "error_code": "chat_access_scope_mismatch",
        "reason": (
            "Access token profile mismatch: "
            "token(profile_id='other-profile') "
            "!= request(profile_id='default')."
        ),
    }
    assert disconnect_code == 4403


def test_chat_progress_ws_rejects_session_scope_mismatch(monkeypatch: MonkeyPatch) -> None:
    """WS progress route should reject different session even within the same profile."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch, profile_id="default", session_id="token-session")
    client = TestClient(create_app())

    # Act
    with client.websocket_connect(
        "/v1/chat/progress/ws?profile_id=default&session_id=api-s",
        headers=auth_headers(),
    ) as ws:
        payload = ws.receive_json()
        try:
            ws.receive_json()
            raise AssertionError("expected websocket disconnect")
        except WebSocketDisconnect as exc:
            disconnect_code = exc.code

    # Assert
    assert payload == {
        "ok": False,
        "error_code": "chat_access_scope_mismatch",
        "reason": (
            "Access token session mismatch: "
            "token(session_id='token-session') "
            "!= request(session_id='api-s')."
        ),
    }
    assert disconnect_code == 4403
