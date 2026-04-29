"""Tests for outbound channel delivery runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channels import (
    ChannelDeliveryTarget,
    ChannelOutboundAttachment,
    ChannelOutboundMessage,
)
from afkbot.services.channels.service import (
    ChannelDeliveryService,
    ChannelDeliveryServiceError,
    get_channel_delivery_diagnostics,
    reset_channel_delivery_diagnostics,
)
from afkbot.services.channels.sender_registry import get_channel_sender_registry, reset_channel_sender_registries
from afkbot.services.channels.telethon_user import TelethonUserServiceError
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings


class _FakeAppRuntime(AppRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        payload: dict[str, object]
        if app == "telegram":
            payload = {"message_id": 77, "chat_id": params["chat_id"]}
        else:
            payload = {"ok": True, "to_email": params["to_email"]}
        self.calls.append(
            {
                "app": app,
                "action": action,
                "ctx": ctx,
                "params": params,
            }
        )
        return ToolResult(ok=True, payload=payload)


class _CrashingAppRuntime:
    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        _ = app, action, ctx, params
        raise RuntimeError("boom")


class _TimeoutOnceTelegramAppRuntime(AppRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._send_attempts = 0

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        self.calls.append(
            {
                "app": app,
                "action": action,
                "ctx": ctx,
                "params": params,
            }
        )
        if app == "telegram" and action == "send_message":
            self._send_attempts += 1
            if self._send_attempts == 1:
                return ToolResult.error(
                    error_code="app_run_failed",
                    reason="Telegram action timed out after 15 seconds",
                )
            return ToolResult(ok=True, payload={"message_id": 77, "chat_id": params["chat_id"]})
        raise AssertionError(f"Unexpected app action: {app}/{action}")


@pytest.fixture(autouse=True)
def _reset_delivery_diagnostics() -> None:
    reset_channel_delivery_diagnostics()
    reset_channel_sender_registries()
    yield
    reset_channel_delivery_diagnostics()
    reset_channel_sender_registries()


async def _seed_profile(settings: Settings) -> None:
    service = ProfileService(settings)
    try:
        await service.create(
            profile_id="default",
            name="Default",
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
    finally:
        await service.shutdown()


async def test_channel_delivery_service_sends_explicit_telegram_target(tmp_path: Path) -> None:
    """Explicit Telegram delivery target should route through app runtime."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    app_runtime = _FakeAppRuntime()
    service = ChannelDeliveryService(settings, app_runtime=app_runtime)

    result = await service.deliver_text(
        profile_id="default",
        session_id="s-1",
        run_id=11,
        target=ChannelDeliveryTarget(
            transport="telegram",
            peer_id="42",
            thread_id="9001",
        ),
        text="hello",
    )

    assert result.transport == "telegram"
    assert result.target == {
        "transport": "telegram",
        "peer_id": "42",
        "thread_id": "9001",
    }
    assert app_runtime.calls[0]["app"] == "telegram"
    assert app_runtime.calls[0]["action"] == "send_message"
    assert app_runtime.calls[0]["params"] == {
        "text": "hello",
        "chat_id": "42",
        "message_thread_id": 9001,
    }


async def test_channel_delivery_service_sends_telegram_rich_message(tmp_path: Path) -> None:
    """Telegram delivery should pass parse mode, buttons, media, and draft streaming options."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery_rich.db'}")
    app_runtime = _FakeAppRuntime()
    service = ChannelDeliveryService(settings, app_runtime=app_runtime)

    result = await service.deliver_message(
        profile_id="default",
        session_id="s-1",
        run_id=12,
        target=ChannelDeliveryTarget(transport="telegram", peer_id="42"),
        message=ChannelOutboundMessage(
            text="*Report* ready",
            parse_mode="MarkdownV2",
            stream_draft=True,
            reply_markup={
                "inline_keyboard": [[{"text": "Open", "url": "https://example.com/report"}]]
            },
            attachments=(
                ChannelOutboundAttachment(
                    kind="document",
                    source="reports/build.txt",
                    caption="Build log",
                ),
            ),
        ),
    )

    assert result.transport == "telegram"
    assert [call["action"] for call in app_runtime.calls] == [
        "send_message_draft",
        "send_document",
        "send_message",
    ]
    assert app_runtime.calls[0]["params"]["text"] == "*Report* ready"
    assert app_runtime.calls[1]["params"] == {
        "document": "reports/build.txt",
        "caption": "Build log",
        "chat_id": "42",
        "reply_markup": {
            "inline_keyboard": [[{"text": "Open", "url": "https://example.com/report"}]]
        },
    }
    assert app_runtime.calls[2]["params"] == {
        "text": "*Report* ready",
        "chat_id": "42",
        "parse_mode": "MarkdownV2",
        "reply_markup": {
            "inline_keyboard": [[{"text": "Open", "url": "https://example.com/report"}]]
        },
    }


async def test_channel_delivery_service_skips_draft_stream_for_non_private_telegram_target(
    tmp_path: Path,
) -> None:
    """Telegram draft previews are private-chat only and should not run for groups."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery_group_draft.db'}")
    app_runtime = _FakeAppRuntime()
    service = ChannelDeliveryService(settings, app_runtime=app_runtime)

    result = await service.deliver_message(
        profile_id="default",
        session_id="s-1",
        run_id=13,
        target=ChannelDeliveryTarget(transport="telegram", peer_id="-10042"),
        message=ChannelOutboundMessage(text="group reply", stream_draft=True),
    )

    assert result.transport == "telegram"
    assert [call["action"] for call in app_runtime.calls] == ["send_message"]


async def test_channel_delivery_service_splits_long_telegram_message(tmp_path: Path) -> None:
    """Telegram delivery should split overlong texts into multiple send_message calls."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    app_runtime = _FakeAppRuntime()
    service = ChannelDeliveryService(settings, app_runtime=app_runtime)

    long_text = ("alpha " * 900).strip()

    result = await service.deliver_text(
        profile_id="default",
        session_id="s-1",
        run_id=111,
        target=ChannelDeliveryTarget(
            transport="telegram",
            peer_id="42",
        ),
        text=long_text,
    )

    assert result.payload["chunk_count"] == 2
    assert len(app_runtime.calls) == 2
    assert all(call["action"] == "send_message" for call in app_runtime.calls)
    assert all(len(str(call["params"]["text"])) <= 4096 for call in app_runtime.calls)


async def test_channel_delivery_service_resolves_binding_target(tmp_path: Path) -> None:
    """Delivery target may reuse explicit transport coordinates from one binding rule."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    await _seed_profile(settings)
    bindings = ChannelBindingService(settings)
    try:
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-sales",
                transport="telegram",
                profile_id="default",
                session_policy="per-thread",
                peer_id="84",
                thread_id="333",
            )
        )
        app_runtime = _FakeAppRuntime()
        service = ChannelDeliveryService(settings, app_runtime=app_runtime, binding_service=bindings)

        result = await service.deliver_turn_result(
            turn_result=TurnResult(
                run_id=21,
                profile_id="default",
                session_id="chat:84:thread:333",
                envelope=ActionEnvelope(action="finalize", message="binding hello"),
            ),
            target=ChannelDeliveryTarget(
                transport="telegram",
                binding_id="telegram-sales",
            ),
        )

        assert result is not None
        assert result.target == {
            "transport": "telegram",
            "binding_id": "telegram-sales",
            "peer_id": "84",
            "thread_id": "333",
        }
        assert app_runtime.calls[0]["params"] == {
            "text": "binding hello",
            "chat_id": "84",
            "message_thread_id": 333,
        }
    finally:
        await bindings.shutdown()


async def test_channel_delivery_service_rejects_binding_without_peer_id(tmp_path: Path) -> None:
    """Telegram delivery should fail when resolved binding lacks peer coordinates."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    await _seed_profile(settings)
    bindings = ChannelBindingService(settings)
    try:
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-missing-peer",
                transport="telegram",
                profile_id="default",
                session_policy="main",
            )
        )
        service = ChannelDeliveryService(settings, app_runtime=_FakeAppRuntime(), binding_service=bindings)

        with pytest.raises(ChannelDeliveryServiceError, match="peer_id"):
            await service.deliver_text(
                profile_id="default",
                session_id="s-1",
                run_id=11,
                target=ChannelDeliveryTarget(
                    transport="telegram",
                    binding_id="telegram-missing-peer",
                ),
                text="hello",
            )
    finally:
        await bindings.shutdown()


async def test_channel_delivery_service_rejects_disabled_binding(tmp_path: Path) -> None:
    """Disabled bindings must not be used for outbound delivery."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    await _seed_profile(settings)
    bindings = ChannelBindingService(settings)
    try:
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-disabled",
                transport="telegram",
                profile_id="default",
                session_policy="main",
                peer_id="84",
                enabled=False,
            )
        )
        service = ChannelDeliveryService(settings, app_runtime=_FakeAppRuntime(), binding_service=bindings)

        with pytest.raises(ChannelDeliveryServiceError, match="disabled"):
            await service.deliver_text(
                profile_id="default",
                session_id="s-1",
                run_id=11,
                target=ChannelDeliveryTarget(
                    transport="telegram",
                    binding_id="telegram-disabled",
                ),
                text="hello",
            )
    finally:
        await bindings.shutdown()


async def test_channel_delivery_service_sends_explicit_smtp_target(tmp_path: Path) -> None:
    """Explicit SMTP delivery target should route through smtp app runtime."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    app_runtime = _FakeAppRuntime()
    service = ChannelDeliveryService(settings, app_runtime=app_runtime)

    result = await service.deliver_text(
        profile_id="default",
        session_id="s-1",
        run_id=12,
        target=ChannelDeliveryTarget(
            transport="smtp",
            address="ops@example.com",
            subject="Build result",
        ),
        text="hello over email",
    )

    assert result.transport == "smtp"
    assert result.target == {
        "transport": "smtp",
        "address": "ops@example.com",
        "subject": "Build result",
    }
    assert app_runtime.calls[0]["app"] == "smtp"
    assert app_runtime.calls[0]["action"] == "send_email"
    assert app_runtime.calls[0]["params"] == {
        "to_email": "ops@example.com",
        "subject": "Build result",
        "body": "hello over email",
    }


async def test_channel_delivery_service_records_unexpected_runtime_exception(tmp_path: Path) -> None:
    """Unexpected transport exceptions should become structured errors and telemetry events."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    service = ChannelDeliveryService(settings, app_runtime=_CrashingAppRuntime())

    with pytest.raises(ChannelDeliveryServiceError, match="boom"):
        await service.deliver_text(
            profile_id="default",
            session_id="s-1",
            run_id=13,
            target=ChannelDeliveryTarget(
                transport="telegram",
                peer_id="42",
            ),
            text="hello",
        )

    diagnostics = get_channel_delivery_diagnostics(settings)
    assert diagnostics.total == 1
    assert diagnostics.failed == 1
    assert diagnostics.succeeded == 0
    assert diagnostics.recent_events[-1].error_code == "channel_delivery_failed"


async def test_channel_delivery_service_retries_one_telegram_timeout(tmp_path: Path) -> None:
    """Telegram send_message should retry once after one transport timeout."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    app_runtime = _TimeoutOnceTelegramAppRuntime()
    service = ChannelDeliveryService(settings, app_runtime=app_runtime)

    result = await service.deliver_text(
        profile_id="default",
        session_id="s-1",
        run_id=113,
        target=ChannelDeliveryTarget(
            transport="telegram",
            peer_id="42",
        ),
        text="hello",
    )

    assert result.transport == "telegram"
    assert len(app_runtime.calls) == 2
    first_ctx = app_runtime.calls[0]["ctx"]
    second_ctx = app_runtime.calls[1]["ctx"]
    assert getattr(first_ctx, "timeout_sec") == settings.tool_timeout_default_sec
    assert getattr(second_ctx, "timeout_sec") == 30


async def test_channel_delivery_service_sends_via_registered_telegram_user_sender(tmp_path: Path) -> None:
    """Telegram user transport should dispatch through the live sender registry."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    service = ChannelDeliveryService(settings, app_runtime=_FakeAppRuntime())
    sent: list[dict[str, object]] = []

    async def _sender(target, text: str):  # type: ignore[no-untyped-def]
        sent.append({"target": target.to_payload(), "text": text})
        return {"message_id": 91}

    registry = get_channel_sender_registry(settings)
    await registry.register(transport="telegram_user", account_id="tg-user", sender=_sender)
    try:
        result = await service.deliver_text(
            profile_id="default",
            session_id="s-1",
            run_id=14,
            target=ChannelDeliveryTarget(
                transport="telegram_user",
                account_id="tg-user",
                peer_id="42",
            ),
            text="hello from userbot",
        )
    finally:
        await registry.unregister(transport="telegram_user", account_id="tg-user", sender=_sender)

    assert result.transport == "telegram_user"
    assert result.target == {
        "transport": "telegram_user",
        "account_id": "tg-user",
        "peer_id": "42",
    }
    assert sent == [
        {
            "target": {
                "transport": "telegram_user",
                "account_id": "tg-user",
                "peer_id": "42",
            },
            "text": "hello from userbot",
        }
    ]


async def test_channel_delivery_service_splits_long_telegram_user_message(tmp_path: Path) -> None:
    """Telegram user transport should split overlong texts before live sender dispatch."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    service = ChannelDeliveryService(settings, app_runtime=_FakeAppRuntime())
    sent: list[str] = []

    async def _sender(target, text: str):  # type: ignore[no-untyped-def]
        _ = target
        sent.append(text)
        return {"message_id": len(sent)}

    registry = get_channel_sender_registry(settings)
    await registry.register(transport="telegram_user", account_id="tg-user", sender=_sender)
    try:
        result = await service.deliver_text(
            profile_id="default",
            session_id="s-1",
            run_id=141,
            target=ChannelDeliveryTarget(
                transport="telegram_user",
                account_id="tg-user",
                peer_id="42",
            ),
            text=("beta " * 900).strip(),
        )
    finally:
        await registry.unregister(transport="telegram_user", account_id="tg-user", sender=_sender)

    assert result.payload["chunk_count"] == 2
    assert len(sent) == 2
    assert all(len(item) <= 4096 for item in sent)


async def test_channel_delivery_service_preserves_structured_telegram_user_sender_error(tmp_path: Path) -> None:
    """Structured live-sender failures should surface without collapsing to generic errors."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    service = ChannelDeliveryService(settings, app_runtime=_FakeAppRuntime())

    async def _sender(target, text: str):  # type: ignore[no-untyped-def]
        _ = target, text
        raise TelethonUserServiceError(
            error_code="telethon_thread_not_supported",
            reason="Telethon user transport does not support outbound thread_id yet.",
        )

    registry = get_channel_sender_registry(settings)
    await registry.register(transport="telegram_user", account_id="tg-user", sender=_sender)
    try:
        with pytest.raises(ChannelDeliveryServiceError) as exc_info:
            await service.deliver_text(
                profile_id="default",
                session_id="s-1",
                run_id=15,
                target=ChannelDeliveryTarget(
                    transport="telegram_user",
                    account_id="tg-user",
                    peer_id="42",
                    thread_id="9001",
                ),
                text="hello from userbot",
            )
    finally:
        await registry.unregister(transport="telegram_user", account_id="tg-user", sender=_sender)

    assert exc_info.value.error_code == "telethon_thread_not_supported"


async def test_channel_delivery_service_preserves_telegram_user_sender_metadata(tmp_path: Path) -> None:
    """Structured live-sender metadata should survive the delivery-service wrapper."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    service = ChannelDeliveryService(settings, app_runtime=_FakeAppRuntime())

    async def _sender(target, text: str):  # type: ignore[no-untyped-def]
        _ = target, text
        raise TelethonUserServiceError(
            error_code="telethon_flood_wait",
            reason="Retry later.",
            metadata={"retry_after_sec": 12},
        )

    registry = get_channel_sender_registry(settings)
    await registry.register(transport="telegram_user", account_id="tg-user", sender=_sender)
    try:
        with pytest.raises(ChannelDeliveryServiceError) as exc_info:
            await service.deliver_text(
                profile_id="default",
                session_id="s-1",
                run_id=16,
                target=ChannelDeliveryTarget(
                    transport="telegram_user",
                    account_id="tg-user",
                    peer_id="42",
                ),
                text="hello from userbot",
            )
    finally:
        await registry.unregister(transport="telegram_user", account_id="tg-user", sender=_sender)

    assert exc_info.value.error_code == "telethon_flood_wait"
    assert exc_info.value.metadata["retry_after_sec"] == 12
