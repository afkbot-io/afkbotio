"""HTTP ingress tests for the automation runtime daemon."""

from __future__ import annotations

import asyncio
from pathlib import Path

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations.runtime_daemon import RuntimeDaemon
from afkbot.services.automations.runtime_daemon_http import RuntimeDaemonHttpRuntime
from afkbot.services.automations.runtime_http import (
    HttpRequest,
    WebhookIngressTarget,
    match_webhook_path,
)
from afkbot.services.automations.service import AutomationsService
from tests.services.automations._runtime_harness import (
    FakeRuntimeService,
    build_settings,
    request_json,
    request_raw,
    webhook_path,
)


def test_match_webhook_path_contract() -> None:
    """Webhook ingress should only accept the canonical endpoint path."""

    assert match_webhook_path("/v1/automations/github/webhook/token-valid") == WebhookIngressTarget(
        profile_id="github",
        token="token-valid",
    )
    assert match_webhook_path("/v1/automations/webhook") is None
    assert match_webhook_path("/v1/automations/github/webhook") is None


async def test_runtime_http_returns_queue_full_when_enqueue_rejects(tmp_path: Path) -> None:
    """Webhook ingress should surface backpressure when runtime queue rejects a task."""

    settings = build_settings(tmp_path)
    runtime = RuntimeDaemonHttpRuntime(
        settings=settings,
        enqueue_task=lambda _task: False,
        is_ready=lambda: True,
        is_shutting_down=lambda: False,
        webhook_token_validator=lambda _profile_id, _token: asyncio.sleep(0, result=True),
        validation_session_factory_getter=lambda: None,
        queue_task_factory=lambda profile_id, token, payload: (profile_id, token, payload),
    )
    status, payload = await runtime.route_request(
        HttpRequest(
            method="POST",
            path=webhook_path(),
            headers={},
            body=b'{"event_id":"evt-full"}',
        )
    )
    assert status == 429
    assert payload["error_code"] == "queue_full"


async def test_runtime_daemon_webhook_endpoints(tmp_path: Path) -> None:
    """Daemon should expose health/ready and webhook endpoint status semantics."""

    service = FakeRuntimeService()

    async def token_validator(profile_id: str, token: str) -> bool:
        return profile_id == "default" and token == "token-valid"

    settings = build_settings(tmp_path)
    daemon = RuntimeDaemon(settings=settings, service=service, webhook_token_validator=token_validator)
    await daemon.start()
    try:
        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="GET",
            path="/healthz",
        )
        assert status == 200
        assert payload == {"ok": True}

        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="GET",
            path="/readyz",
        )
        assert status == 200
        assert payload == {"ok": True}

        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-invalid"),
            body='{"event_id":"evt-1"}',
        )
        assert status == 401
        assert payload["error_code"] == "invalid_webhook_token"

        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-valid"),
            body='["invalid"]',
        )
        assert status == 400
        assert payload["error_code"] == "invalid_payload"

        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-valid"),
            body='{"event_id":"evt-2"}',
        )
        assert status == 202
        assert payload == {"accepted": True}

        for _ in range(20):
            if service.webhook_calls:
                break
            await asyncio.sleep(0.01)
        assert service.webhook_calls == [("default", "token-valid", {"event_id": "evt-2"})]

        daemon.begin_shutdown()
        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-valid"),
            body='{"event_id":"evt-3"}',
        )
        assert status == 503
        assert payload["error_code"] == "runtime_shutting_down"
    finally:
        await daemon.stop()


async def test_runtime_daemon_rejects_chunked_transfer_encoding(tmp_path: Path) -> None:
    """Daemon should reject unsupported transfer-encoding payloads."""

    service = FakeRuntimeService()
    settings = build_settings(tmp_path)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _profile_id, _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        status, payload = await request_raw(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-valid"),
            headers={"Transfer-Encoding": "chunked"},
            body="4\r\n{}\r\n0\r\n\r\n",
        )
        assert status == 400
        assert payload is not None
        assert payload["error_code"] == "unsupported_transfer_encoding"
    finally:
        await daemon.stop()


async def test_runtime_daemon_rejects_payload_over_limit(tmp_path: Path) -> None:
    """Daemon should return 413 for payloads exceeding configured limit."""

    service = FakeRuntimeService()
    settings = build_settings(tmp_path, runtime_max_body_bytes=8)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _profile_id, _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-valid"),
            body='{"event_id":"evt-oversized"}',
        )
        assert status == 413
        assert payload["error_code"] == "payload_too_large"
    finally:
        await daemon.stop()


async def test_runtime_daemon_rejects_headers_over_limit(tmp_path: Path) -> None:
    """Daemon should return 413 when header block exceeds configured limit."""

    service = FakeRuntimeService()
    settings = build_settings(tmp_path, runtime_max_header_bytes=64)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _profile_id, _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(token="token-valid"),
            headers={"X-Long": "a" * 256},
            body='{"event_id":"evt-header-limit"}',
        )
        assert status == 413
        assert payload["error_code"] == "header_too_large"
    finally:
        await daemon.stop()


async def test_runtime_daemon_default_token_validation_path(tmp_path: Path) -> None:
    """Daemon should validate webhook token against the stored webhook target when validator is absent."""

    settings = build_settings(tmp_path)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(session_factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
        service = AutomationsService(session_factory, settings=settings)
        created = await service.create_webhook(
            profile_id="default",
            name="db-validation",
            prompt="test prompt",
        )
        token = created.webhook.webhook_token if created.webhook is not None else ""
        assert token

        fake_service = FakeRuntimeService()
        daemon = RuntimeDaemon(settings=settings, service=fake_service)
        await daemon.start()
        try:
            invalid_status, invalid_payload = await request_json(
                host=settings.runtime_host,
                port=daemon.bound_port,
                method="POST",
                path=webhook_path(token="wrong-token"),
                body='{"event_id":"evt-db-invalid"}',
            )
            assert invalid_status == 401
            assert invalid_payload["error_code"] == "invalid_webhook_token"

            valid_status, valid_payload = await request_json(
                host=settings.runtime_host,
                port=daemon.bound_port,
                method="POST",
                path=webhook_path(token=token),
                body='{"event_id":"evt-db-valid"}',
            )
            assert valid_status == 202
            assert valid_payload == {"accepted": True}
        finally:
            await daemon.stop()
    finally:
        await engine.dispose()
