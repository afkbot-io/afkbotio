"""Shared harness objects and builders for Telethon user-channel runtime tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.services.channels.endpoint_contracts import (
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
    TelethonUserEndpointConfig,
    TelethonWatcherConfig,
)
from afkbot.services.channels.endpoint_service import ChannelEndpointService
from afkbot.services.channels.telethon_user.watcher import TelethonWatchedEvent
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService
from afkbot.settings import Settings


class FakeDeliveryService:
    """Capture delivered texts for assertions without touching transport adapters."""

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
    ) -> object:
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


class FakeTelethonClient:
    """Minimal Telethon-like client used by runtime tests."""

    def __init__(self, *, dialogs: list[object] | None = None) -> None:
        self.handler = None
        self.builder = None
        self.disconnect_event = asyncio.Event()
        self.sent_messages: list[dict[str, object]] = []
        self.read_ack_calls: list[dict[str, object]] = []
        self.typing_calls: list[dict[str, object]] = []
        self.dialogs = [] if dialogs is None else list(dialogs)

    async def connect(self) -> None:
        return

    async def disconnect(self) -> None:
        self.disconnect_event.set()

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> object:
        return SimpleNamespace(
            id=1001,
            username="afkme",
            phone="+79990000000",
            first_name="Afk",
            last_name="Me",
        )

    def add_event_handler(self, handler, builder) -> None:  # type: ignore[no-untyped-def]
        self.handler = handler
        self.builder = builder

    def remove_event_handler(self, handler, builder) -> None:  # type: ignore[no-untyped-def]
        if self.builder == builder:
            self.handler = None
            self.builder = None

    async def run_until_disconnected(self) -> None:
        await self.disconnect_event.wait()

    async def send_message(self, entity: object, text: str) -> object:
        self.sent_messages.append({"entity": entity, "text": text})
        return SimpleNamespace(id=91)

    async def get_dialogs(self, limit: object = None) -> list[object]:
        _ = limit
        return list(self.dialogs)

    async def send_read_acknowledge(self, entity: object, max_id: int | None = None) -> None:
        self.read_ack_calls.append({"entity": entity, "max_id": max_id})

    def action(self, entity: object, action_name: str) -> object:
        client = self

        class ActionContext:
            async def __aenter__(self_nonlocal) -> None:
                client.typing_calls.append({"entity": entity, "action": action_name})

            async def __aexit__(self_nonlocal, exc_type, exc, tb) -> bool:
                _ = exc_type, exc, tb
                return False

        return ActionContext()


class FakeFloodWaitError(Exception):
    """Simple exception carrying a Telethon-style flood-wait `seconds` field."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"A wait of {seconds} seconds is required")
        self.seconds = seconds


async def seed_profile_and_binding(settings: Settings) -> None:
    """Create the default runtime profile, binding, and endpoint for Telethon tests."""

    engine = create_engine(settings)
    await create_schema(engine)
    await engine.dispose()
    profiles = ProfileService(settings)
    bindings = ChannelBindingService(settings)
    endpoints = ChannelEndpointService(settings)
    try:
        await profiles.create(
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
            policy_network_allowlist=("*",),
        )
        await bindings.put(
            ChannelBindingRule(
                binding_id="telethon-main",
                transport="telegram_user",
                account_id="tg-user",
                profile_id="default",
                session_policy="per-chat",
                prompt_overlay="Reply as userbot.",
            )
        )
        await endpoints.create(
            endpoint(
                endpoint_id="telethon-main",
                profile_id="default",
                credential_profile_key="tg-user-main",
                account_id="tg-user",
                reply_mode="same_chat",
            )
        )
    finally:
        await endpoints.shutdown()
        await bindings.shutdown()
        await profiles.shutdown()


def endpoint(
    *,
    endpoint_id: str = "telethon-main",
    profile_id: str = "default",
    credential_profile_key: str = "tg-user-main",
    account_id: str = "tg-user",
    enabled: bool = True,
    reply_mode: str = "same_chat",
    reply_blocked_chat_patterns: tuple[str, ...] = (),
    reply_allowed_chat_patterns: tuple[str, ...] = (),
    group_invocation_mode: str = "reply_or_command",
    process_self_commands: bool = False,
    ingress_batch: ChannelIngressBatchConfig | None = None,
    reply_humanization: ChannelReplyHumanizationConfig | None = None,
    mark_read_before_reply: bool = True,
    watcher: TelethonWatcherConfig | None = None,
) -> TelethonUserEndpointConfig:
    """Build one Telethon endpoint config with overridable runtime flags."""

    return TelethonUserEndpointConfig(
        endpoint_id=endpoint_id,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        account_id=account_id,
        enabled=enabled,
        reply_mode=reply_mode,
        reply_blocked_chat_patterns=reply_blocked_chat_patterns,
        reply_allowed_chat_patterns=reply_allowed_chat_patterns,
        group_invocation_mode=group_invocation_mode,
        process_self_commands=process_self_commands,
        ingress_batch=ingress_batch or ChannelIngressBatchConfig(),
        reply_humanization=reply_humanization or ChannelReplyHumanizationConfig(),
        mark_read_before_reply=mark_read_before_reply,
        watcher=watcher or TelethonWatcherConfig(),
    )


def fake_dialog(
    *,
    chat_id: int,
    chat_kind: str,
    title: str,
    muted_until: datetime | int | None = None,
) -> object:
    """Build one fake Telethon dialog snapshot for watcher tests."""

    return SimpleNamespace(
        id=chat_id,
        name=title,
        is_user=chat_kind == "private",
        is_group=chat_kind == "group",
        is_channel=chat_kind == "channel",
        dialog=SimpleNamespace(
            notify_settings=SimpleNamespace(mute_until=muted_until),
        ),
        entity=SimpleNamespace(
            id=chat_id,
            title=title,
            first_name=title if chat_kind == "private" else None,
            last_name=None,
            username=None,
        ),
    )


def watched_event(*, event_key: str, chat_id: str, message_id: int) -> TelethonWatchedEvent:
    """Build one watcher event with stable defaults for buffer-management tests."""

    return TelethonWatchedEvent(
        event_key=event_key,
        message_id=message_id,
        chat_id=chat_id,
        chat_kind="group",
        chat_title=f"Chat {chat_id}",
        sender_id="777",
        text="message",
        observed_at=datetime.now(UTC).isoformat(),
    )


async def fake_resolve_credentials(**kwargs: object) -> object:
    """Return stable fake Telethon credentials for runtime tests."""

    _ = kwargs
    return SimpleNamespace(
        api_id=12345,
        api_hash="hash",
        session_string="session",
        phone="+79990000000",
    )
