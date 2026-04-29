"""Tests for connect REST API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.connect import (
    ConnectClaimResult,
    ConnectClientMetadata,
    ConnectRefreshResult,
    ConnectServiceError,
)
from afkbot.version import load_cli_version_info

_APP_VERSION = load_cli_version_info(root_dir=Path(__file__).resolve().parents[2]).version


def test_connect_claim_route_delegates_to_service(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/connect/claim should call claim service and return typed payload."""

    async def _fake_claim(
        *,
        claim_token: str,
        claim_pin: str | None = None,
        client: ConnectClientMetadata | None = None,
        access_ttl_sec: int = 3600,
        refresh_ttl_sec: int = 2592000,
    ) -> ConnectClaimResult:
        _ = access_ttl_sec, refresh_ttl_sec
        assert claim_token == "claim-1"
        assert claim_pin == "2468"
        assert client == ConnectClientMetadata(platform="desktop", app_version=_APP_VERSION)
        return ConnectClaimResult(
            access_token="acc-1",
            refresh_token="ref-1",
            session_proof="proof-1",
            expires_in_sec=3600,
            base_url="http://127.0.0.1:8081",
            profile_id="default",
            session_id="desktop-session",
        )

    monkeypatch.setattr("afkbot.api.routes_connect.claim_connect_token", _fake_claim)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/connect/claim",
            json={
                "claim_token": "claim-1",
                "claim_pin": "2468",
                "client": {
                    "platform": "desktop",
                    "app_version": _APP_VERSION,
                },
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "access_token": "acc-1",
        "refresh_token": "ref-1",
        "session_proof": "proof-1",
        "expires_in_sec": 3600,
        "base_url": "http://127.0.0.1:8081",
        "profile_id": "default",
        "session_id": "desktop-session",
    }


def test_create_app_uses_cli_version_for_openapi_metadata() -> None:
    """API version should stay aligned with the package version used by update checks."""

    app = create_app()
    assert app.version == _APP_VERSION


def test_connect_refresh_route_delegates_to_service(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/connect/refresh should validate refresh token via service."""

    async def _fake_refresh(
        *,
        refresh_token: str,
        session_proof: str | None = None,
        session_id: str | None = None,
        access_ttl_sec: int = 3600,
    ) -> ConnectRefreshResult:
        _ = access_ttl_sec
        assert refresh_token == "ref-1"
        assert session_proof == "proof-1"
        assert session_id == "desktop-session-2"
        return ConnectRefreshResult(
            access_token="acc-2",
            refresh_token="ref-2",
            expires_in_sec=3600,
            session_id="desktop-session-2",
        )

    monkeypatch.setattr("afkbot.api.routes_connect.refresh_connect_access_token", _fake_refresh)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/connect/refresh",
            json={"refresh_token": "ref-1", "session_proof": "proof-1", "session_id": "desktop-session-2"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "access_token": "acc-2",
        "refresh_token": "ref-2",
        "expires_in_sec": 3600,
        "session_id": "desktop-session-2",
    }


def test_connect_revoke_route_delegates_to_service(monkeypatch: MonkeyPatch) -> None:
    """POST /v1/connect/revoke should revoke refresh token through service."""

    async def _fake_revoke(*, refresh_token: str, session_proof: str | None = None) -> bool:
        assert refresh_token == "ref-1"
        assert session_proof == "proof-1"
        return True

    monkeypatch.setattr("afkbot.api.routes_connect.revoke_connect_session", _fake_revoke)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/connect/revoke", json={"refresh_token": "ref-1", "session_proof": "proof-1"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_connect_claim_route_maps_service_error(monkeypatch: MonkeyPatch) -> None:
    """Connect service errors should map to deterministic HTTP payload."""

    async def _fake_claim(
        *,
        claim_token: str,
        claim_pin: str | None = None,
        client: ConnectClientMetadata | None = None,
        access_ttl_sec: int = 3600,
        refresh_ttl_sec: int = 2592000,
    ) -> ConnectClaimResult:
        _ = claim_token, claim_pin, client, access_ttl_sec, refresh_ttl_sec
        raise ConnectServiceError(error_code="connect_token_expired", reason="expired")

    monkeypatch.setattr("afkbot.api.routes_connect.claim_connect_token", _fake_claim)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/connect/claim", json={"claim_token": "claim-1"})

    assert response.status_code == 410
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "connect_token_expired",
            "reason": "expired",
        }
    }


def test_connect_refresh_route_maps_issue_error_to_500(monkeypatch: MonkeyPatch) -> None:
    """Token-issuance failures should map to HTTP 500 contract."""

    async def _fake_refresh(
        *,
        refresh_token: str,
        session_proof: str | None = None,
        session_id: str | None = None,
        access_ttl_sec: int = 3600,
    ) -> ConnectRefreshResult:
        _ = refresh_token, session_proof, session_id, access_ttl_sec
        raise ConnectServiceError(error_code="connect_access_issue_failed", reason="issue failed")

    monkeypatch.setattr("afkbot.api.routes_connect.refresh_connect_access_token", _fake_refresh)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/connect/refresh", json={"refresh_token": "ref-1", "session_proof": "proof-1"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "connect_access_issue_failed",
            "reason": "issue failed",
        }
    }


def test_connect_refresh_route_maps_session_profile_mismatch_to_conflict(
    monkeypatch: MonkeyPatch,
) -> None:
    """Session/profile scope conflicts should map to HTTP 409."""

    async def _fake_refresh(
        *,
        refresh_token: str,
        session_proof: str | None = None,
        session_id: str | None = None,
        access_ttl_sec: int = 3600,
    ) -> ConnectRefreshResult:
        _ = refresh_token, session_proof, session_id, access_ttl_sec
        raise ConnectServiceError(
            error_code="connect_session_profile_mismatch",
            reason="session belongs to another profile",
        )

    monkeypatch.setattr("afkbot.api.routes_connect.refresh_connect_access_token", _fake_refresh)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/connect/refresh", json={"refresh_token": "ref-1", "session_proof": "proof-1"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "connect_session_profile_mismatch",
            "reason": "session belongs to another profile",
        }
    }


def test_connect_refresh_route_rejects_session_ids_over_runtime_limit() -> None:
    """Refresh request validation should enforce the shared runtime session-id limit."""

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/connect/refresh",
            json={"refresh_token": "ref-1", "session_proof": "proof-1", "session_id": "x" * 65},
        )

    assert response.status_code == 422


def test_connect_refresh_route_maps_rate_limit_to_429(monkeypatch: MonkeyPatch) -> None:
    """Connect rate limits should propagate deterministic 429 payload and Retry-After."""

    async def _fake_refresh(
        *,
        refresh_token: str,
        session_proof: str | None = None,
        session_id: str | None = None,
        access_ttl_sec: int = 3600,
    ) -> ConnectRefreshResult:
        _ = refresh_token, session_proof, session_id, access_ttl_sec
        raise AssertionError("service must not run after rate limit")

    async def _fake_limit(
        *,
        settings: object,
        scope: str,
        remote_host: str | None,
        token_value: str | None = None,
    ) -> None:
        _ = settings, remote_host, token_value
        assert scope == "refresh"
        raise ConnectServiceError(
            error_code="connect_rate_limited",
            reason="Too many connect refresh requests. Retry after 7 seconds.",
            retry_after_sec=7,
        )

    monkeypatch.setattr("afkbot.api.routes_connect.refresh_connect_access_token", _fake_refresh)
    monkeypatch.setattr("afkbot.api.routes_connect.enforce_connect_rate_limit", _fake_limit)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/connect/refresh", json={"refresh_token": "ref-1", "session_proof": "proof-1"})

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "connect_rate_limited",
            "reason": "Too many connect refresh requests. Retry after 7 seconds.",
        }
    }
