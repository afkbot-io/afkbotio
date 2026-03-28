"""Tests for chat API binding-aware target resolution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channel_routing import (
    ChannelBindingRule,
    ChannelBindingService,
    reset_channel_binding_services_async,
)
from afkbot.services.connect import ConnectAccessTokenContext
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService, reset_profile_services_async
from afkbot.settings import Settings
from tests.api.chat_api._harness import auth_headers, patch_api_settings


@pytest.fixture(autouse=True)
def _cleanup_cached_services() -> None:
    """Dispose cached async DB services between tests to avoid loop-close warnings."""

    yield
    asyncio.run(reset_channel_binding_services_async())
    asyncio.run(reset_profile_services_async())


async def _seed_profile_and_binding(settings: Settings) -> None:
    profiles = ProfileService(settings)
    bindings = ChannelBindingService(settings)
    try:
        await profiles.create(
            profile_id="sales",
            name="Sales",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=(),
        )
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-sales",
                transport="telegram",
                profile_id="sales",
                session_policy="per-thread",
                peer_id="42",
                prompt_overlay="Always respond as the sales routing agent.",
            )
        )
    finally:
        await bindings.shutdown()
        await profiles.shutdown()


def test_chat_turn_route_resolves_binding_after_auth_and_before_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chat turn route should reject binding target that escapes the access-token session."""

    settings = patch_api_settings(monkeypatch, tmp_path)
    asyncio.run(_seed_profile_and_binding(settings))

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        assert access_token == "acc-1"
        assert session_proof == "proof-1"
        _ = session_factory
        return ConnectAccessTokenContext(
            profile_id="sales",
            session_id="token-session",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "resolve_binding": True,
            "transport": "telegram",
            "peer_id": "42",
            "thread_id": "9001",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_access_scope_mismatch",
            "reason": (
                "Access token session mismatch: "
                "token(session_id='token-session') "
                "!= request(session_id='profile:sales:chat:42:thread:9001')."
            ),
        }
    }


def test_chat_turn_route_rejects_binding_resolution_without_transport(monkeypatch: MonkeyPatch) -> None:
    """Binding-aware API chat request should reject missing transport metadata."""

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        _ = access_token, session_factory
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={"message": "hello", "resolve_binding": True},
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "channel_binding_transport_required",
            "reason": "Transport is required when binding resolution is enabled.",
        }
    }


def test_chat_turn_route_forwards_plan_only_and_thinking_level(
    monkeypatch: MonkeyPatch,
) -> None:
    """Chat API should pass planning/thinking turn overrides into runtime execution."""

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        _ = access_token, session_factory
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

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

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "implement feature",
            "plan_only": True,
            "thinking_level": "very-high",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["envelope"]["message"] == "plan"


def test_chat_turn_route_forwards_execution_planning_mode(
    monkeypatch: MonkeyPatch,
) -> None:
    """Chat API should forward explicit execution planning mode when provided."""

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        _ = access_token, session_factory
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

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

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "implement feature",
            "planning_mode": "on",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["envelope"]["message"] == "execute"


def test_chat_turn_route_can_require_binding_match(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Strict binding mode should reject unresolved selectors instead of falling back."""

    patch_api_settings(monkeypatch, tmp_path)

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        _ = access_token, session_factory
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "resolve_binding": True,
            "require_binding_match": True,
            "transport": "telegram",
            "peer_id": "42",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "channel_binding_no_match",
            "reason": "No channel binding matched the provided target selectors.",
        }
    }


def test_chat_turn_route_external_binding_resolution_is_strict_by_default(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """External transport binding resolution should fail closed without explicit strict flag."""

    patch_api_settings(monkeypatch, tmp_path)

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        _ = access_token, session_factory
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "resolve_binding": True,
            "transport": "telegram",
            "peer_id": "42",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "channel_binding_no_match"


def test_chat_turn_route_hides_binding_errors_without_authorization(tmp_path: Path) -> None:
    """Binding-related requests should still fail on missing auth before routing details leak."""

    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "hello",
            "resolve_binding": True,
            "require_binding_match": True,
            "transport": "telegram",
            "peer_id": "42",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "chat_access_token_missing",
            "reason": "Authorization header with Bearer token is required.",
        }
    }
