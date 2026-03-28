"""Shared Telegram polling test harness and fake runtime adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.endpoint_contracts import (
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
    TelegramPollingEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    get_channel_endpoint_service,
    reset_channel_endpoint_services_async,
)
from afkbot.services.channels.ingress_persistence import (
    reset_channel_ingress_pending_services_async,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService, reset_profile_services_async
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings


@pytest.fixture(autouse=True)
async def _reset_cached_services() -> None:
    """Dispose cached profile, binding, and ingress services around each polling test."""

    await reset_channel_endpoint_services_async()
    await reset_channel_binding_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()
    yield
    await reset_channel_endpoint_services_async()
    await reset_channel_binding_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()


class FakeAppRuntime(AppRuntime):
    """Minimal Telegram app runtime fake used by polling service tests."""

    def __init__(self, *, updates: list[dict[str, object]]) -> None:
        self.updates = updates
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        """Record app calls and return deterministic Telegram payloads."""

        self.calls.append(
            {
                "app": app,
                "action": action,
                "ctx": ctx,
                "params": params,
            }
        )
        if action == "get_me":
            return ToolResult(ok=True, payload={"id": 1001, "username": "afkbot"})
        if action == "get_updates":
            return ToolResult(ok=True, payload={"updates": list(self.updates)})
        if action == "send_chat_action":
            return ToolResult(ok=True, payload={"ok": True, "action": "send_chat_action"})
        raise AssertionError(f"Unexpected app action: {action}")


class FakeDeliveryService:
    """Collect outbound Telegram replies instead of calling the real delivery service."""

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
        """Record one delivery call and return a success payload."""

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


class FlakyTimeoutingDeliveryService(FakeDeliveryService):
    """Timeout once, then succeed on the next delivery attempt."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

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
        """Raise one transient timeout before delegating to the base fake."""

        self.attempts += 1
        if self.attempts == 1:
            raise ChannelDeliveryServiceError(
                error_code="app_run_failed",
                reason="Telegram action timed out after 30 seconds",
            )
        return await super().deliver_text(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            target=target,
            text=text,
            credential_profile_key=credential_profile_key,
        )


class TimeoutingGetUpdatesAppRuntime(FakeAppRuntime):
    """Return one timeout error for Telegram `get_updates`."""

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        """Return deterministic `getMe`, `send_chat_action`, and timeout results."""

        self.calls.append(
            {
                "app": app,
                "action": action,
                "ctx": ctx,
                "params": params,
            }
        )
        if action == "get_me":
            return ToolResult(ok=True, payload={"id": 1001, "username": "afkbot"})
        if action == "get_updates":
            return ToolResult.error(
                error_code="app_run_failed",
                reason="Telegram action timed out after 25 seconds",
            )
        if action == "send_chat_action":
            return ToolResult(ok=True, payload={"ok": True, "action": "send_chat_action"})
        raise AssertionError(f"Unexpected app action: {action}")


class TimeoutingChatActionAppRuntime(FakeAppRuntime):
    """Return one timeout error for Telegram `send_chat_action`."""

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: object,
        params: dict[str, object],
    ) -> ToolResult:
        """Return deterministic `getMe`, `getUpdates`, and typing-timeout results."""

        self.calls.append(
            {
                "app": app,
                "action": action,
                "ctx": ctx,
                "params": params,
            }
        )
        if action == "get_me":
            return ToolResult(ok=True, payload={"id": 1001, "username": "afkbot"})
        if action == "get_updates":
            return ToolResult(ok=True, payload={"updates": list(self.updates)})
        if action == "send_chat_action":
            return ToolResult.error(
                error_code="app_run_failed",
                reason="Telegram action timed out after 15 seconds",
            )
        raise AssertionError(f"Unexpected app action: {action}")


async def seed_profile_and_binding(settings: Settings) -> None:
    """Seed one profile, endpoint, and default Telegram binding for polling tests."""

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
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.telegram.org",),
        )
        await endpoints.create(endpoint())
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-main",
                transport="telegram",
                account_id="telegram-bot",
                profile_id="default",
                session_policy="per-thread",
                prompt_overlay="Reply as Telegram bot.",
            )
        )
    finally:
        await bindings.shutdown()
        await profiles.shutdown()


def endpoint(
    *,
    endpoint_id: str = "telegram-main",
    profile_id: str = "default",
    credential_profile_key: str = "bot-main",
    account_id: str = "telegram-bot",
    enabled: bool = True,
    group_trigger_mode: str = "mention_or_reply",
    ingress_batch: ChannelIngressBatchConfig | None = None,
    reply_humanization: ChannelReplyHumanizationConfig | None = None,
) -> TelegramPollingEndpointConfig:
    """Build one Telegram polling endpoint config for tests."""

    return TelegramPollingEndpointConfig(
        endpoint_id=endpoint_id,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        account_id=account_id,
        enabled=enabled,
        group_trigger_mode=group_trigger_mode,
        ingress_batch=ingress_batch or ChannelIngressBatchConfig(),
        reply_humanization=reply_humanization or ChannelReplyHumanizationConfig(),
    )


def load_state_payload(*, settings: Settings) -> dict[str, object]:
    """Load the persisted Telegram polling state file for the default endpoint."""

    state_path = get_channel_endpoint_service(settings).telegram_polling_state_path(
        endpoint_id="telegram-main"
    )
    return json.loads(state_path.read_text(encoding="utf-8"))


def default_state_path(*, settings: Settings) -> Path:
    """Return the default polling state path for the seeded test endpoint."""

    return get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main")
