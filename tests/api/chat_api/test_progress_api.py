"""HTTP progress route tests for chat API."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.agent_loop.api_runtime import ProgressPollResponse
from afkbot.services.agent_loop.progress_stream import ProgressCursor, ProgressEvent
from tests.api.chat_api._harness import auth_headers, patch_valid_chat_access_token


def test_chat_progress_route_returns_cursor_and_events(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/chat/progress should return canonical progress payload."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    async def _fake_poll(
        *,
        profile_id: str,
        session_id: str,
        cursor: ProgressCursor,
    ) -> ProgressPollResponse:
        assert profile_id == "default"
        assert session_id == "api-s"
        assert cursor.run_id == 7
        assert cursor.last_event_id == 3
        return ProgressPollResponse(
            events=[
                ProgressEvent(
                    event_id=4,
                    run_id=7,
                    stage="thinking",
                    iteration=1,
                    tool_name=None,
                    event_type="turn.progress",
                )
            ],
            cursor=ProgressCursor(run_id=7, last_event_id=4),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.poll_chat_progress", _fake_poll)
    client = TestClient(create_app())

    # Act
    response = client.get(
        "/v1/chat/progress",
        params={"profile_id": "default", "session_id": "api-s", "run_id": 7, "after_event_id": 3},
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json() == {
        "events": [
            {
                "event_id": 4,
                "run_id": 7,
                "stage": "thinking",
                "iteration": 1,
                "tool_name": None,
                "event_type": "turn.progress",
                "payload": {},
            }
        ],
        "cursor": {"run_id": 7, "last_event_id": 4},
    }


def test_chat_progress_route_rejects_scope_mismatch(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/chat/progress should return deterministic 403 for profile mismatch."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch, profile_id="other-profile")
    client = TestClient(create_app())

    # Act
    response = client.get(
        "/v1/chat/progress",
        params={"profile_id": "default", "session_id": "api-s"},
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_access_scope_mismatch",
            "reason": (
                "Access token profile mismatch: "
                "token(profile_id='other-profile') "
                "!= request(profile_id='default')."
            ),
        }
    }


def test_chat_progress_route_rejects_session_scope_mismatch(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/chat/progress should reject different session within the same profile."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch, profile_id="default", session_id="token-session")
    client = TestClient(create_app())

    # Act
    response = client.get(
        "/v1/chat/progress",
        params={"profile_id": "default", "session_id": "api-s"},
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_access_scope_mismatch",
            "reason": (
                "Access token session mismatch: "
                "token(session_id='token-session') "
                "!= request(session_id='api-s')."
            ),
        }
    }
