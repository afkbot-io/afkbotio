"""Interaction routes for chat answer, secure-field, and catalog APIs."""

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


def test_chat_answer_route_delegates_to_interactive_resume(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/answer should proxy ask-question answers into resume service."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)
    captured: dict[str, object] = {}

    async def _fake_resume_chat_interaction(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        envelope = kwargs["envelope"]
        assert isinstance(envelope, ActionEnvelope)
        return TurnResult(
            run_id=77,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="approved"),
        )

    async def _resolved_envelope(
        *,
        profile_id: str,
        session_id: str,
        question_id: str,
        action: str,
        secure_field: str | None = None,
    ) -> ActionEnvelope:
        assert profile_id == "default"
        assert session_id == "api-s"
        assert question_id == "approval-1"
        assert action == "ask_question"
        assert secure_field is None
        return ActionEnvelope(
            action="ask_question",
            message="confirm",
            question_id="approval-1",
            spec_patch={"tool_name": "debug.echo", "tool_params": {"message": "ok"}},
        )

    monkeypatch.setattr(
        "afkbot.api.chat_routes.http.resolve_pending_question_envelope",
        _resolved_envelope,
    )
    monkeypatch.setattr(
        "afkbot.api.chat_routes.http.resume_chat_interaction",
        _fake_resume_chat_interaction,
    )
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/answer",
        json={
            "profile_id": "default",
            "session_id": "api-s",
            "question_id": "approval-1",
            "approved": True,
            "spec_patch": {
                "tool_name": "debug.echo",
                "tool_params": {"message": "ok"},
            },
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["run_id"] == 77
    assert captured["approved"] is True
    assert captured["profile_id"] == "default"
    assert captured["session_id"] == "api-s"


def test_chat_answer_route_rejects_unknown_pending_question(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/answer should reject unknown question ids."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    async def _missing(**kwargs: object) -> ActionEnvelope | None:
        _ = kwargs
        return None

    monkeypatch.setattr("afkbot.api.chat_routes.http.resolve_pending_question_envelope", _missing)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/answer",
        json={
            "profile_id": "default",
            "session_id": "api-s",
            "question_id": "approval-unknown",
            "approved": True,
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "chat_request_invalid"


def test_chat_secure_field_route_delegates_to_submit(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/secure-field should call secure submit helper."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    async def _fake_submit(
        *,
        profile_id: str,
        envelope: object,
        secret_value: str,
        session_id: str | None = None,
    ) -> tuple[bool, str]:
        _ = envelope
        assert profile_id == "default"
        assert secret_value == "s3cr3t"
        assert session_id == "api-s"
        return True, "ok"

    async def _resolved_secure_envelope(
        *,
        profile_id: str,
        session_id: str,
        question_id: str,
        action: str,
        secure_field: str | None = None,
    ) -> ActionEnvelope:
        assert profile_id == "default"
        assert session_id == "api-s"
        assert question_id == "secure:qid"
        assert action == "request_secure_field"
        assert secure_field == "telegram_token"
        return ActionEnvelope(
            action="request_secure_field",
            message="secure",
            question_id="secure:qid",
            secure_field="telegram_token",
            spec_patch={
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "secure_nonce": "nonce-1",
            },
        )

    monkeypatch.setattr(
        "afkbot.api.chat_routes.http.resolve_pending_question_envelope",
        _resolved_secure_envelope,
    )
    monkeypatch.setattr("afkbot.api.chat_routes.http.submit_secure_field", _fake_submit)
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/secure-field",
        json={
            "profile_id": "default",
            "session_id": "api-s",
            "question_id": "secure:qid",
            "secure_field": "telegram_token",
            "secret_value": "s3cr3t",
            "spec_patch": {
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "secure_nonce": "nonce-1",
            },
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json() == {"ok": True, "error_code": "ok", "next_turn": None}


def test_chat_secure_field_route_can_resume_after_submit(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/chat/secure-field should optionally resume next turn after storing secret."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)
    captured: dict[str, object] = {}

    async def _fake_submit(
        *,
        profile_id: str,
        envelope: object,
        secret_value: str,
        session_id: str | None = None,
    ) -> tuple[bool, str]:
        _ = envelope
        assert profile_id == "default"
        assert secret_value == "s3cr3t"
        assert session_id == "api-s"
        return True, "ok"

    async def _fake_resume_after_secure_submit(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        return TurnResult(
            run_id=91,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    async def _resolved_secure_resume_envelope(**kwargs: object) -> ActionEnvelope:
        _ = kwargs
        return ActionEnvelope(
            action="request_secure_field",
            message="secure",
            question_id="secure:qid",
            secure_field="telegram_token",
            spec_patch={
                "tool_name": "app.run",
                "tool_params": {"app_name": "telegram", "action": "get_me"},
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "secure_nonce": "nonce-1",
            },
        )

    monkeypatch.setattr(
        "afkbot.api.chat_routes.http.resolve_pending_question_envelope",
        _resolved_secure_resume_envelope,
    )
    monkeypatch.setattr("afkbot.api.chat_routes.http.submit_secure_field", _fake_submit)
    monkeypatch.setattr(
        "afkbot.api.chat_routes.http.resume_chat_after_secure_submit",
        _fake_resume_after_secure_submit,
    )
    client = TestClient(create_app())

    # Act
    response = client.post(
        "/v1/chat/secure-field",
        json={
            "profile_id": "default",
            "session_id": "api-s",
            "question_id": "secure:qid",
            "secure_field": "telegram_token",
            "secret_value": "s3cr3t",
            "resume_after_submit": True,
            "spec_patch": {
                "tool_name": "app.run",
                "tool_params": {"app_name": "telegram", "action": "get_me"},
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "secure_nonce": "nonce-1",
            },
        },
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["next_turn"]["run_id"] == 91
    assert isinstance(captured["envelope"], ActionEnvelope)


def test_chat_catalog_route_returns_runtime_mentions(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/chat/catalog should proxy normalized runtime metadata for the app shell."""

    # Arrange
    patch_valid_chat_access_token(monkeypatch)

    class _FakeCatalogService:
        async def get_catalog(self, *, profile_id: str, session_id: str) -> dict[str, object]:
            assert profile_id == "default"
            assert session_id == "api-s"
            return {
                "profile_id": profile_id,
                "profile_name": "Default",
                "session_id": session_id,
                "runtime": {
                    "llm_provider": "openai",
                    "llm_model": "gpt-5",
                    "thinking_level": "medium",
                    "planning_mode": "auto",
                    "policy_preset": "medium",
                    "file_access_mode": "read_write",
                    "capabilities": ["files", "shell"],
                },
                "mentions": [
                    {
                        "kind": "skill",
                        "slug": "telegram",
                        "title": "Telegram",
                        "description": "Telegram Bot and app-run workflows.",
                        "aliases": ["tg"],
                        "origin": "core",
                        "available": True,
                    },
                    {
                        "kind": "subagent",
                        "slug": "researcher",
                        "title": "Researcher",
                        "description": "Collect factual context from files and contracts.",
                        "aliases": [],
                        "origin": "core",
                        "available": True,
                    },
                ],
            }

    monkeypatch.setattr(
        "afkbot.api.chat_routes.http.get_app_catalog_service",
        lambda settings: _FakeCatalogService(),
    )
    client = TestClient(create_app())

    # Act
    response = client.get(
        "/v1/chat/catalog",
        params={"profile_id": "default", "session_id": "api-s"},
        headers=auth_headers(),
    )

    # Assert
    assert response.status_code == 200
    assert response.json() == {
        "profile_id": "default",
        "profile_name": "Default",
        "session_id": "api-s",
        "runtime": {
            "llm_provider": "openai",
            "llm_model": "gpt-5",
            "thinking_level": "medium",
            "planning_mode": "auto",
            "policy_preset": "medium",
            "file_access_mode": "read_write",
            "capabilities": ["files", "shell"],
        },
        "mentions": [
            {
                "kind": "skill",
                "slug": "telegram",
                "title": "Telegram",
                "description": "Telegram Bot and app-run workflows.",
                "aliases": ["tg"],
                "origin": "core",
                "available": True,
            },
            {
                "kind": "subagent",
                "slug": "researcher",
                "title": "Researcher",
                "description": "Collect factual context from files and contracts.",
                "aliases": [],
                "origin": "core",
                "available": True,
            },
        ],
    }


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_chat_turn_uses_routing_snapshot_bound_to_real_connect_token(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chat route should merge trusted routing snapshot carried by real connect access token."""

    # Arrange
    settings = patch_api_settings(monkeypatch, tmp_path)
    seed_profile_sync(settings)
    issued = asyncio.run(
        issue_connect_url(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            ttl_sec=120,
            context_overrides=TurnContextOverrides(
                runtime_metadata={
                    "transport": "desktop",
                    "peer_id": "workspace-7",
                    "channel_binding": {
                        "binding_id": "desktop-sales",
                        "session_policy": "per-thread",
                    },
                },
                prompt_overlay="Always respond as the desktop workspace assistant.",
            ),
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
        assert context_overrides is not None
        assert context_overrides.runtime_metadata == {
            "transport": "desktop",
            "peer_id": "workspace-7",
            "channel_binding": {
                "binding_id": "desktop-sales",
                "session_policy": "per-thread",
            },
        }
        assert (
            context_overrides.prompt_overlay == "Always respond as the desktop workspace assistant."
        )
        return TurnResult(
            run_id=8,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)

    # Act
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/turn",
            json={"message": "hello"},
            headers=auth_headers(token=claimed.access_token, proof=claimed.session_proof),
        )

    # Assert
    assert response.status_code == 200
    assert response.json()["run_id"] == 8


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_chat_turn_ignores_request_routing_selectors_when_token_has_snapshot(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Token-bound routing snapshot should stay authoritative for non-resolve-binding requests."""

    # Arrange
    settings = patch_api_settings(monkeypatch, tmp_path)
    seed_profile_sync(settings)
    issued = asyncio.run(
        issue_connect_url(
            profile_id="default",
            session_id="api-s",
            base_url="http://127.0.0.1:8081",
            ttl_sec=120,
            context_overrides=TurnContextOverrides(
                runtime_metadata={"transport": "desktop", "peer_id": "workspace-7"},
                prompt_overlay="Always answer as the desktop workspace assistant.",
            ),
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
        assert context_overrides is not None
        assert context_overrides.runtime_metadata == {
            "transport": "desktop",
            "peer_id": "workspace-7",
        }
        return TurnResult(
            run_id=9,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    monkeypatch.setattr("afkbot.api.chat_routes.http.run_chat_turn", _fake_turn)

    # Act
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/turn",
            json={
                "message": "hello",
                "transport": "telegram",
                "peer_id": "42",
                "thread_id": "7",
            },
            headers=auth_headers(token=claimed.access_token, proof=claimed.session_proof),
        )

    # Assert
    assert response.status_code == 200
    assert response.json()["run_id"] == 9
