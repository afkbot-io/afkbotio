"""PartyFlow polling runtime tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.endpoint_contracts import (
    ChannelAccessPolicy,
    ChannelIngressBatchConfig,
    PartyFlowPollingEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    get_channel_endpoint_service,
    reset_channel_endpoint_services_async,
)
from afkbot.services.channels.ingress_journal import reset_channel_ingress_journal_services_async
from afkbot.services.channels.ingress_persistence import (
    get_channel_ingress_pending_service,
    reset_channel_ingress_pending_services_async,
)
from afkbot.services.channels.service import ChannelDeliveryServiceError
from afkbot.services.channels.partyflow_polling import PartyFlowPollingService
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService, reset_profile_services_async
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings


@pytest.fixture(autouse=True)
async def _reset_cached_services() -> None:
    await reset_channel_binding_services_async()
    await reset_channel_endpoint_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()
    yield
    await reset_channel_binding_services_async()
    await reset_channel_endpoint_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()


class _FakePartyFlowRuntime:
    def __init__(self, *, events: list[dict[str, object]], next_cursor: str = "cursor-2") -> None:
        self.events = events
        self.next_cursor = next_cursor
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        self.calls.append({"app": app, "action": action, "ctx": ctx, "params": params})
        if action == "get_me":
            return ToolResult(ok=True, payload={"bot": {"id": "bot-42", "display_name": "Bot"}})
        if action == "poll_events":
            return ToolResult(
                ok=True,
                payload={"events": list(self.events), "next_cursor": self.next_cursor},
            )
        raise AssertionError(f"Unexpected action: {action}")


class _FakeDeliveryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def deliver_text(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ChannelDeliveryTarget,
        text: str,
        credential_profile_key: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "run_id": run_id,
                "target": target,
                "text": text,
                "credential_profile_key": credential_profile_key,
            }
        )
        return {"ok": True}


async def _seed_profile_and_binding(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    await engine.dispose()
    profiles = ProfileService(settings)
    bindings = ChannelBindingService(settings)
    endpoints = get_channel_endpoint_service(settings)
    try:
        await profiles.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(llm_provider="openai", llm_model="gpt-4o-mini"),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.partyflow.ru",),
        )
        await endpoints.create(_endpoint())
        await bindings.put(
            ChannelBindingRule(
                binding_id="partyflow-main",
                transport="partyflow",
                account_id="partyflow-bot",
                profile_id="default",
                session_policy="per-thread",
            )
        )
    finally:
        await endpoints.shutdown()
        await bindings.shutdown()
        await profiles.shutdown()


def _endpoint(
    *,
    trigger_mode: str = "mention",
    trigger_keywords: tuple[str, ...] = (),
    ingress_batch: ChannelIngressBatchConfig | None = None,
    access_policy: ChannelAccessPolicy | None = None,
) -> PartyFlowPollingEndpointConfig:
    return PartyFlowPollingEndpointConfig(
        endpoint_id="partyflow-main",
        profile_id="default",
        credential_profile_key="partyflow-main",
        account_id="partyflow-bot",
        trigger_mode=trigger_mode,
        trigger_keywords=trigger_keywords,
        ingress_batch=ingress_batch or ChannelIngressBatchConfig(),
        access_policy=access_policy
        or ChannelAccessPolicy(private_policy="open", group_policy="open"),
    )


def _message_event(
    *,
    event_id: str,
    text: str,
    author_id: str = "user-1",
    mentions: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "MESSAGE_CREATED",
        "created_at": "2026-05-12T10:00:00Z",
        "payload_json": json.dumps(
            {
                "id": f"msg-{event_id}",
                "conversation_id": "conv-1",
                "thread_id": "thread-9",
                "author_id": author_id,
                "content": text,
                "mentions": [{"id": "bot-42"}] if mentions is None else mentions,
            },
            ensure_ascii=True,
        ),
        "next_cursor": "cursor-ignored",
    }


async def test_partyflow_polling_processes_message_and_persists_cursor(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_polling.db'}",
    )
    await _seed_profile_and_binding(settings)
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(kwargs)
        return TurnResult(
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            run_id=7,
            envelope=ActionEnvelope(action="finalize", message="partyflow reply"),
        )

    delivery = _FakeDeliveryService()
    app_runtime = _FakePartyFlowRuntime(events=[_message_event(event_id="evt-1", text="hello")])
    state_path = get_channel_endpoint_service(settings).partyflow_polling_state_path(
        endpoint_id="partyflow-main"
    )
    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(),
        state_path=state_path,
        app_runtime=app_runtime,  # type: ignore[arg-type]
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    processed = await service.poll_once()

    assert processed == 1
    assert captured[0]["message"] == "hello"
    assert captured[0]["client_msg_id"] == "partyflow:partyflow-bot:msg-evt-1"
    assert delivery.calls[0]["text"] == "partyflow reply"
    assert app_runtime.calls[0]["ctx"].approved_tool_names == ("app.run",)
    assert app_runtime.calls[0]["ctx"].approved_network_hosts == ("api.partyflow.ru",)
    assert json.loads(state_path.read_text(encoding="utf-8"))["cursor"] == "cursor-2"


async def test_partyflow_polling_reuses_persisted_cursor(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_cursor.db'}",
    )
    await _seed_profile_and_binding(settings)
    state_path = get_channel_endpoint_service(settings).partyflow_polling_state_path(
        endpoint_id="partyflow-main"
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"account_id":"partyflow-bot","cursor":"cursor-1"}', encoding="utf-8")
    runtime = _FakePartyFlowRuntime(events=[], next_cursor="")
    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(),
        state_path=state_path,
        app_runtime=runtime,  # type: ignore[arg-type]
    )

    await service.poll_once()

    poll_call = next(item for item in runtime.calls if item["action"] == "poll_events")
    assert poll_call["params"]["cursor"] == "cursor-1"


async def test_partyflow_polling_failure_does_not_advance_cursor_or_keep_claim(
    tmp_path: Path,
) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_invalid_payload.db'}",
    )
    await _seed_profile_and_binding(settings)
    state_path = get_channel_endpoint_service(settings).partyflow_polling_state_path(
        endpoint_id="partyflow-main"
    )
    invalid_event = {
        "event_id": "evt-bad",
        "event_type": "MESSAGE_CREATED",
        "created_at": "2026-05-12T10:00:00Z",
        "payload_json": "{",
    }
    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(trigger_mode="all"),
        state_path=state_path,
        app_runtime=_FakePartyFlowRuntime(events=[invalid_event]),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="payload_json must be valid JSON"):
        await service.poll_once()

    assert not state_path.exists()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(kwargs)
        return TurnResult(
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            run_id=10,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    retry_service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(trigger_mode="all"),
        state_path=state_path,
        app_runtime=_FakePartyFlowRuntime(
            events=[_message_event(event_id="evt-bad", text="fixed payload")]
        ),  # type: ignore[arg-type]
        channel_delivery_service=_FakeDeliveryService(),  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    processed = await retry_service.poll_once()

    assert processed == 1
    assert captured[0]["message"] == "fixed payload"
    assert json.loads(state_path.read_text(encoding="utf-8"))["cursor"] == "cursor-2"


async def test_partyflow_polling_default_runtime_uses_credentials_and_http_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_direct.db'}",
    )
    await _seed_profile_and_binding(settings)
    captured: list[dict[str, object]] = []

    class _FakeCredentialsService:
        async def resolve_plaintext_for_app_tool(self, **kwargs: object) -> str:
            captured.append({"kind": "credential", **kwargs})
            return "fri_bot_test"

    async def _fake_get_me(**kwargs: object) -> dict[str, object]:
        captured.append({"kind": "get_me", **kwargs})
        return {"bot": {"id": "bot-42", "display_name": "Bot"}}

    async def _fake_poll_events(**kwargs: object) -> dict[str, object]:
        captured.append({"kind": "poll_events", **kwargs})
        return {"events": [], "next_cursor": "cursor-direct"}

    monkeypatch.setattr(
        "afkbot.services.channels.partyflow_polling.get_credentials_service",
        lambda _settings: _FakeCredentialsService(),
    )
    monkeypatch.setattr("afkbot.services.channels.partyflow_polling._get_me", _fake_get_me)
    monkeypatch.setattr(
        "afkbot.services.channels.partyflow_polling._poll_events",
        _fake_poll_events,
    )
    state_path = get_channel_endpoint_service(settings).partyflow_polling_state_path(
        endpoint_id="partyflow-main"
    )
    service = PartyFlowPollingService(settings, endpoint=_endpoint(), state_path=state_path)

    processed = await service.poll_once()

    assert processed == 0
    assert [item["kind"] for item in captured] == [
        "credential",
        "get_me",
        "credential",
        "poll_events",
    ]
    poll_call = next(item for item in captured if item["kind"] == "poll_events")
    assert poll_call["token"] == "fri_bot_test"
    assert poll_call["cursor"] == ""
    assert json.loads(state_path.read_text(encoding="utf-8"))["cursor"] == "cursor-direct"


async def test_partyflow_polling_mention_trigger_falls_back_to_text_handle(
    tmp_path: Path,
) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_text_mention.db'}",
    )
    await _seed_profile_and_binding(settings)
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(kwargs)
        return TurnResult(
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            run_id=9,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(trigger_mode="mention"),
        state_path=get_channel_endpoint_service(settings).partyflow_polling_state_path(
            endpoint_id="partyflow-main"
        ),
        app_runtime=_FakePartyFlowRuntime(
            events=[
                _message_event(event_id="evt-1", text="@Bot hello", mentions=[]),
                _message_event(event_id="evt-2", text="@Botany ignored", mentions=[]),
            ]
        ),  # type: ignore[arg-type]
        channel_delivery_service=_FakeDeliveryService(),  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    await service.poll_once()

    assert [item["message"] for item in captured] == ["@Bot hello"]


async def test_partyflow_polling_keyword_trigger_uses_token_boundaries(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_keywords.db'}",
    )
    await _seed_profile_and_binding(settings)
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(kwargs)
        return TurnResult(
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            run_id=8,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(trigger_mode="keywords", trigger_keywords=("deploy",)),
        state_path=get_channel_endpoint_service(settings).partyflow_polling_state_path(
            endpoint_id="partyflow-main"
        ),
        app_runtime=_FakePartyFlowRuntime(
            events=[
                _message_event(event_id="evt-1", text="redeployment ignored", mentions=[]),
                _message_event(event_id="evt-2", text="deploy now", mentions=[]),
            ]
        ),  # type: ignore[arg-type]
        channel_delivery_service=_FakeDeliveryService(),  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    await service.poll_once()

    assert [item["message"] for item in captured] == ["deploy now"]


async def test_partyflow_polling_retries_failed_pending_batch_until_success(
    tmp_path: Path,
) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_pending_retry.db'}",
    )
    await _seed_profile_and_binding(settings)
    attempts = 0
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise ChannelDeliveryServiceError(
                error_code="partyflow_retryable",
                reason="temporary delivery backoff",
                metadata={"retry_after_sec": 0},
            )
        captured.append(kwargs)
        return TurnResult(
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            run_id=11,
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    state_path = get_channel_endpoint_service(settings).partyflow_polling_state_path(
        endpoint_id="partyflow-main"
    )
    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(
            trigger_mode="all",
            ingress_batch=ChannelIngressBatchConfig(enabled=True, debounce_ms=100),
        ),
        state_path=state_path,
        app_runtime=_FakePartyFlowRuntime(
            events=[_message_event(event_id="evt-1", text="retry me")]
        ),  # type: ignore[arg-type]
        channel_delivery_service=_FakeDeliveryService(),  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    processed = await service.poll_once()
    for _ in range(20):
        if attempts >= 3:
            break
        await asyncio.sleep(0.01)

    pending = await get_channel_ingress_pending_service(settings).list_pending(
        endpoint_id="partyflow-main"
    )
    assert processed == 1
    assert attempts == 3
    assert captured[0]["message"] == "retry me"
    assert pending == []
    assert json.loads(state_path.read_text(encoding="utf-8"))["cursor"] == "cursor-2"


async def test_partyflow_polling_stop_cancels_rescheduled_pending_retry(
    tmp_path: Path,
) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_pending_retry_stop.db'}",
    )
    await _seed_profile_and_binding(settings)
    attempts = 0

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        nonlocal attempts
        attempts += 1
        raise ChannelDeliveryServiceError(
            error_code="partyflow_retryable",
            reason="temporary delivery backoff",
            metadata={"retry_after_sec": 0 if attempts == 1 else 60},
        )

    state_path = get_channel_endpoint_service(settings).partyflow_polling_state_path(
        endpoint_id="partyflow-main"
    )
    service = PartyFlowPollingService(
        settings,
        endpoint=_endpoint(
            trigger_mode="all",
            ingress_batch=ChannelIngressBatchConfig(enabled=True, debounce_ms=100),
        ),
        state_path=state_path,
        app_runtime=_FakePartyFlowRuntime(
            events=[_message_event(event_id="evt-1", text="retry me")]
        ),  # type: ignore[arg-type]
        channel_delivery_service=_FakeDeliveryService(),  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    await service.poll_once()
    for _ in range(20):
        if attempts >= 2:
            break
        await asyncio.sleep(0.01)
    assert attempts == 2
    assert len(service._pending_retry_tasks) == 1  # type: ignore[attr-defined]

    await service.stop()
    await asyncio.sleep(0.05)

    pending = await get_channel_ingress_pending_service(settings).list_pending(
        endpoint_id="partyflow-main"
    )
    assert attempts == 2
    assert len(service._pending_retry_tasks) == 0  # type: ignore[attr-defined]
    assert len(pending) == 1
