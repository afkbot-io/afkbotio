"""Tests for PartyFlow webhook ingress routes."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app


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


def test_partyflow_webhook_route_returns_404_without_registered_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    """API webhook route should fail closed when the endpoint runtime is not active."""

    monkeypatch.setattr(
        "afkbot.api.routes_partyflow_webhooks.get_partyflow_webhook_runtime_registry",
        lambda _settings: type("_Registry", (), {"get": lambda self, endpoint_id: None})(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/v1/channels/partyflow/missing/webhook",
        json={"event_type": "MESSAGE_CREATED"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "partyflow_channel_not_active",
            "reason": "PartyFlow channel runtime is not active: missing",
        }
    }
