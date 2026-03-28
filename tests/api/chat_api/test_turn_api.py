"""HTTP chat turn route tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect import claim_connect_token, issue_connect_url
from tests.api.chat_api._harness import (
    auth_headers,
    patch_api_settings,
    patch_valid_chat_access_token,
    seed_profile_sync,
)


def test_chat_turn_route_delegates_to_runtime(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/turn should proxy request into runtime turn execution."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    async def _fake_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        assert message == "hello"
        assert profile_id == "default"
        assert session_id == "api-s"
        assert client_msg_id == "msg-1"
        assert context_overrides is None
        return TurnResult(
            run_id=42,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "profile_id": "default",
            "session_id": "api-s",
            "client_msg_id": "msg-1",
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json() == {
        "run_id": 42,
        "session_id": "api-s",
        "profile_id": "default",
        "envelope": {
            "action": "finalize",
            "message": "ok",
            "question_id": None,
            "spec_patch": None,
            "secure_field": None,
            "blocked_reason": None,
        },
    }


def test_chat_turn_route_rejects_invalid_thinking_level(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/turn should return deterministic 400 for invalid thinking levels."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "profile_id": "default",
            "session_id": "api-s",
            "thinking_level": "turbo",
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_request_invalid",
            "reason": "thinking_level must be one of: low, medium, high, very_high",
        }
    }


def test_chat_turn_route_rejects_invalid_profile_id(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/turn should reject unsafe profile ids with 400 payload."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "profile_id": "Default",
            "session_id": "api-s",
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_request_invalid",
            "reason": "Invalid profile id: Default",
        }
    }


def test_chat_turn_route_defaults_to_access_token_session(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/turn should default to the access token session when request omits it."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch, session_id="token-session")

    async def _fake_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        assert message == "hello"
        assert profile_id == "default"
        assert session_id == "token-session"
        assert client_msg_id is None
        assert context_overrides is None
        return TurnResult(
            run_id=43,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={"message": "hello"},
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["session_id"] == "token-session"


def test_chat_turn_route_rejects_session_scope_mismatch(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/turn should reject explicit session outside the access-token scope."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch, session_id="token-session")
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={"message": "hello", "session_id": "other-session"},
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
                "!= request(session_id='other-session')."
            ),
        }
    }


def test_chat_turn_route_requires_authorization() -> None:
    """POST /v1/chat/turn should reject requests without Bearer token."""

    # Arrange
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "profile_id": "default",
            "session_id": "api-s",
        },
    )

    # Assert
    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_access_token_missing",
            "reason": "Authorization header with Bearer token is required.",
        }
    }


def test_chat_turn_route_forwards_plan_only_and_thinking_level(monkeypatch: MonkeyPatch) -> None:
    """Chat API should pass planning and thinking overrides into runtime execution."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    async def _fake_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        assert message == "implement feature"
        assert profile_id == "default"
        assert session_id == "api-s"
        assert client_msg_id is None
        assert context_overrides is not None
        assert context_overrides.planning_mode == "plan_only"
        assert context_overrides.thinking_level == "very_high"
        return TurnResult(
            run_id=19,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="plan"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "implement feature",
            "plan_only": True,
            "thinking_level": "very-high",
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["envelope"]["message"] == "plan"


def test_chat_turn_route_forwards_execution_planning_mode(monkeypatch: MonkeyPatch) -> None:
    """Chat API should forward explicit execution planning mode when provided."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    async def _fake_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        assert message == "implement feature"
        assert profile_id == "default"
        assert session_id == "api-s"
        assert client_msg_id is None
        assert context_overrides is not None
        assert context_overrides.planning_mode == "off"
        assert context_overrides.execution_planning_mode == "on"
        return TurnResult(
            run_id=20,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="execute"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/turn",
        json={"message": "implement feature", "planning_mode": "on"},
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["envelope"]["message"] == "execute"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_chat_turn_accepts_real_connect_access_token_without_auth_monkeypatch(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chat route should validate real connect access tokens via shared API runtime resources."""

    # Arrange
    settings = patch_api_settings(monkeypatch, tmp_path)
    seed_profile_sync(settings)
    issued = asyncio.run(
        issue_connect_url(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            ttl_sec=120,
        )
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = asyncio.run(claim_connect_token(claim_token=claim_token))

    async def _fake_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        assert message == "hello"
        assert profile_id == "default"
        assert session_id == "api-s"
        assert client_msg_id is None
        assert context_overrides is None
        return TurnResult(
            run_id=7,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)

    # Act
    with TestClient(create_app()) as client:
        monkeypatch.setattr(
            "afkbot.services.connect.access.create_engine",
            lambda settings: (_ for _ in ()).throw(
                AssertionError("create_engine should not run in auth path")
            ),
        )
        response = client.post(
            "/v1/chat/turn",
            json={"message": "hello", "profile_id": "default", "session_id": "api-s"},
            headers=auth_headers(token=claimed.access_token, proof=claimed.session_proof),
        )

    # Assert
    assert response.status_code == 200
    assert response.json()["run_id"] == 7
