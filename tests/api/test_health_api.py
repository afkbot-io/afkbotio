"""Tests for operator-facing health diagnostics API routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.connect import ConnectAccessTokenContext
from afkbot.services.channel_routing import (
    ChannelRoutingDiagnostics,
    ChannelRoutingTelemetryEvent,
    ChannelRoutingTransportDiagnostics,
)
from afkbot.services.channels.contracts import (
    ChannelDeliveryDiagnostics,
    ChannelDeliveryTelemetryEvent,
    ChannelDeliveryTransportDiagnostics,
)
from afkbot.services.health import (
    DoctorChannelsReport,
    DoctorDeliveryReport,
    DoctorRoutingReport,
    TelegramPollingEndpointReport,
)


def _allow_health_auth(monkeypatch: MonkeyPatch) -> None:
    async def _fake_auth(*, authorization: str | None, session_proof: str | None = None) -> ConnectAccessTokenContext:
        assert authorization == "Bearer test-token"
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="desktop-session",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
            allow_diagnostics=True,
        )

    monkeypatch.setattr("afkbot.api.routes_health.require_chat_http_context", _fake_auth)


def test_api_health_and_ready_routes() -> None:
    """API should expose basic health and readiness probes."""

    # Arrange
    client = TestClient(create_app())

    # Act
    health = client.get("/healthz")
    ready = client.get("/readyz")

    # Assert
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "afkbot-api"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_health_routes_require_auth() -> None:
    """Operator health routes should reject unauthenticated access."""

    app = create_app()
    client = TestClient(app)

    response = client.get("/v1/health/routing")

    assert response.status_code == 401


def test_health_routes_require_diagnostics_scope(monkeypatch: MonkeyPatch) -> None:
    """Health diagnostics should reject ordinary chat-scoped tokens."""

    async def _fake_auth(*, authorization: str | None, session_proof: str | None = None) -> ConnectAccessTokenContext:
        assert authorization == "Bearer test-token"
        assert session_proof == "proof-1"
        return ConnectAccessTokenContext(
            profile_id="default",
            session_id="desktop-session",
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
            allow_diagnostics=False,
        )

    monkeypatch.setattr("afkbot.api.routes_health.require_chat_http_context", _fake_auth)
    app = create_app()
    client = TestClient(app)

    response = client.get(
        "/v1/health/routing",
        headers={"Authorization": "Bearer test-token", "X-AFK-Session-Proof": "proof-1"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "connect_diagnostics_scope_required",
            "reason": "Access token is not allowed to read operator diagnostics.",
        }
    }


def test_health_routing_route_returns_diagnostics(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/health/routing should expose structured routing diagnostics."""

    _allow_health_auth(monkeypatch)

    async def _fake_report(_settings: object) -> DoctorRoutingReport:
        return DoctorRoutingReport(
            diagnostics=ChannelRoutingDiagnostics(
                total=5,
                matched=3,
                fallback_used=1,
                no_match=2,
                strict_no_match=1,
                transports=(
                    ChannelRoutingTransportDiagnostics(
                        transport="telegram",
                        total=4,
                        matched=3,
                        fallback_used=0,
                        no_match=1,
                        strict_no_match=1,
                    ),
                ),
                recent_events=(
                    ChannelRoutingTelemetryEvent(
                        transport="telegram",
                        strict=True,
                        matched=False,
                        no_match=True,
                        fallback_used=False,
                        peer_id="42",
                    ),
                ),
            ),
            fallback_transports=("api", "automation"),
        )

    monkeypatch.setattr("afkbot.api.routes_health.run_channel_routing_diagnostics", _fake_report)
    app = create_app()
    client = TestClient(app)

    response = client.get(
        "/v1/health/routing",
        headers={"Authorization": "Bearer test-token", "X-AFK-Session-Proof": "proof-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "fallback_transports": ["api", "automation"],
        "totals": {
            "total": 5,
            "matched": 3,
            "fallback_used": 1,
            "no_match": 2,
            "strict_no_match": 1,
        },
        "transports": [
            {
                "transport": "telegram",
                "total": 4,
                "matched": 3,
                "fallback_used": 0,
                "no_match": 1,
                "strict_no_match": 1,
            }
        ],
        "recent_events": [
            {
                "transport": "telegram",
                "strict": True,
                "matched": False,
                "no_match": True,
                "fallback_used": False,
                "account_id": None,
                "peer_id": "42",
                "thread_id": None,
                "user_id": None,
                "binding_id": None,
                "profile_id": None,
                "session_policy": None,
                "prompt_overlay_applied": False,
            }
        ],
    }


def test_health_delivery_route_returns_diagnostics(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/health/delivery should expose structured outbound delivery diagnostics."""

    _allow_health_auth(monkeypatch)

    async def _fake_report(_settings: object) -> DoctorDeliveryReport:
        return DoctorDeliveryReport(
            diagnostics=ChannelDeliveryDiagnostics(
                total=4,
                succeeded=3,
                failed=1,
                transports=(
                    ChannelDeliveryTransportDiagnostics(
                        transport="smtp",
                        total=2,
                        succeeded=1,
                        failed=1,
                    ),
                ),
                recent_events=(
                    ChannelDeliveryTelemetryEvent(
                        transport="smtp",
                        ok=False,
                        error_code="channel_delivery_failed",
                        address="ops@example.com",
                        subject="Build result",
                    ),
                ),
            )
        )

    monkeypatch.setattr("afkbot.api.routes_health.run_channel_delivery_diagnostics", _fake_report)
    app = create_app()
    client = TestClient(app)

    response = client.get(
        "/v1/health/delivery",
        headers={"Authorization": "Bearer test-token", "X-AFK-Session-Proof": "proof-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "totals": {
            "total": 4,
            "succeeded": 3,
            "failed": 1,
        },
        "transports": [
            {
                "transport": "smtp",
                "total": 2,
                "succeeded": 1,
                "failed": 1,
            }
        ],
        "recent_events": [
            {
                "transport": "smtp",
                "ok": False,
                "error_code": "channel_delivery_failed",
                "binding_id": None,
                "account_id": None,
                "peer_id": None,
                "thread_id": None,
                "user_id": None,
                "address": "ops@example.com",
                "subject": "Build result",
            }
        ],
    }


def test_health_channels_route_returns_channel_status(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/health/channels should expose configured adapter status."""

    _allow_health_auth(monkeypatch)

    async def _fake_report(_settings: object) -> DoctorChannelsReport:
        return DoctorChannelsReport(
            telegram_polling=(
                TelegramPollingEndpointReport(
                    endpoint_id="support-bot",
                    enabled=True,
                    profile_id="default",
                    credential_profile_key="bot-main",
                    account_id="telegram-bot",
                    profile_valid=True,
                    profile_exists=True,
                    token_configured=True,
                    binding_count=2,
                    state_path="/tmp/telegram_polling_state.json",
                    state_present=False,
                ),
            )
        )

    monkeypatch.setattr("afkbot.api.routes_health.run_channel_health_diagnostics", _fake_report)
    app = create_app()
    client = TestClient(app)

    response = client.get(
        "/v1/health/channels",
        headers={"Authorization": "Bearer test-token", "X-AFK-Session-Proof": "proof-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "telegram_polling": {
            "total_endpoints": 1,
            "enabled_endpoints": 1,
            "endpoints": [
                {
                    "endpoint_id": "support-bot",
                    "enabled": True,
                    "profile_id": "default",
                    "credential_profile_key": "bot-main",
                    "account_id": "telegram-bot",
                    "profile_valid": True,
                    "profile_exists": True,
                    "token_configured": True,
                    "binding_count": 2,
                    "state_path": "/tmp/telegram_polling_state.json",
                    "state_present": False,
                }
            ],
        },
        "telethon_userbot": {
            "total_endpoints": 0,
            "enabled_endpoints": 0,
            "endpoints": [],
        },
    }
