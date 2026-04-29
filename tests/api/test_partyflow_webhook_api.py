"""Tests for PartyFlow webhook ingress routes."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.channels.endpoint_contracts import PartyFlowWebhookEndpointConfig
from afkbot.services.channels.endpoint_service import ChannelEndpointServiceError


def test_partyflow_webhook_route_delegates_to_registered_runtime(monkeypatch: MonkeyPatch) -> None:
    """API webhook route should delegate raw request data to the registered PartyFlow runtime."""

    captured: dict[str, object] = {}

    class _FakeRuntime:
        async def handle_webhook(
            self,
            *,
            headers: dict[str, str],
            body: bytes,
        ) -> tuple[int, dict[str, object]]:
            captured["headers"] = headers
            captured["body"] = body
            return 202, {"accepted": True}

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_partyflow_webhook_runtime_registry",
        lambda _settings: type(
            "_Registry",
            (),
            {
                "get": lambda self, endpoint_id: (
                    _FakeRuntime() if endpoint_id == "partyflow-main" else None
                )
            },
        )(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/v1/channels/partyflow/partyflow-main/webhook",
        headers={
            "X-PartyFlow-Delivery-Id": "01923f5c-a2c8-7890-b4d0-5a2c8a4b6e0c",
            "X-PartyFlow-Timestamp": "1744934400",
            "X-PartyFlow-Signature": "sha256=test",
        },
        json={"event_type": "MESSAGE_CREATED"},
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert captured["headers"]["x-partyflow-delivery-id"] == "01923f5c-a2c8-7890-b4d0-5a2c8a4b6e0c"
    assert b'"event_type":"MESSAGE_CREATED"' in captured["body"]


def test_partyflow_webhook_route_returns_503_for_configured_but_inactive_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    """API webhook route should keep PartyFlow retries alive when endpoint config exists."""

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_partyflow_webhook_runtime_registry",
        lambda _settings: type("_Registry", (), {"get": lambda self, endpoint_id: None})(),
    )

    class _EndpointService:
        async def get(self, *, endpoint_id: str) -> object:
            return PartyFlowWebhookEndpointConfig(
                endpoint_id=endpoint_id,
                profile_id="default",
                credential_profile_key=endpoint_id,
                account_id="partyflow-bot",
            )

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_channel_endpoint_service",
        lambda _settings: _EndpointService(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/v1/channels/partyflow/missing/webhook",
        json={"event_type": "MESSAGE_CREATED"},
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {
        "ok": False,
        "error_code": "partyflow_channel_not_active",
        "reason": "PartyFlow channel runtime is not active: missing",
        "retry_after": 1,
    }


def test_partyflow_webhook_route_returns_non_retryable_disabled_response(
    monkeypatch: MonkeyPatch,
) -> None:
    """Disabled PartyFlow endpoints should not ask PartyFlow to keep retrying."""

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_partyflow_webhook_runtime_registry",
        lambda _settings: type("_Registry", (), {"get": lambda self, endpoint_id: None})(),
    )

    class _EndpointService:
        async def get(self, *, endpoint_id: str) -> object:
            return PartyFlowWebhookEndpointConfig(
                endpoint_id=endpoint_id,
                profile_id="default",
                credential_profile_key=endpoint_id,
                account_id="partyflow-bot",
                enabled=False,
            )

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_channel_endpoint_service",
        lambda _settings: _EndpointService(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/v1/channels/partyflow/disabled/webhook",
        json={"event_type": "MESSAGE_CREATED"},
    )

    assert response.status_code == 410
    assert "Retry-After" not in response.headers
    assert response.json() == {
        "ok": False,
        "error_code": "partyflow_channel_disabled",
        "reason": "PartyFlow channel endpoint is disabled: disabled",
    }


def test_partyflow_webhook_route_returns_404_for_unknown_endpoint(
    monkeypatch: MonkeyPatch,
) -> None:
    """API webhook route should keep 404 only for endpoints that are not configured at all."""

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_partyflow_webhook_runtime_registry",
        lambda _settings: type("_Registry", (), {"get": lambda self, endpoint_id: None})(),
    )

    class _MissingEndpointService:
        async def get(self, *, endpoint_id: str) -> object:
            raise ChannelEndpointServiceError(
                error_code="channel_endpoint_not_found",
                reason=f"Channel endpoint not found: {endpoint_id}",
            )

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_channel_endpoint_service",
        lambda _settings: _MissingEndpointService(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/v1/channels/partyflow/unknown/webhook",
        json={"event_type": "MESSAGE_CREATED"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "partyflow_channel_not_found",
            "reason": "PartyFlow channel endpoint is not configured: unknown",
        }
    }


def test_partyflow_webhook_route_sets_retry_after_header_from_runtime_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    """API webhook route should surface Retry-After when runtime asks PartyFlow to retry later."""

    class _FakeRuntime:
        async def handle_webhook(
            self,
            *,
            headers: dict[str, str],
            body: bytes,
        ) -> tuple[int, dict[str, object]]:
            return 429, {"ok": False, "retry_after": 7}

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_partyflow_webhook_runtime_registry",
        lambda _settings: type(
            "_Registry",
            (),
            {
                "get": lambda self, endpoint_id: _FakeRuntime()
                if endpoint_id == "partyflow-main"
                else None
            },
        )(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/v1/channels/partyflow/partyflow-main/webhook",
        headers={
            "X-PartyFlow-Delivery-Id": "01923f5c-a2c8-7890-b4d0-5a2c8a4b6e0c",
            "X-PartyFlow-Timestamp": "1744934400",
            "X-PartyFlow-Signature": "sha256=test",
        },
        json={"event_type": "MESSAGE_CREATED"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
