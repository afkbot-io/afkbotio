"""Helpers for plugin channel runtimes to route inbound messages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib

from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.api_runtime import run_chat_turn
from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.access_policy import is_channel_message_allowed
from afkbot.services.channels.active_context import build_active_channel_context_overrides
from afkbot.services.channels.context_overrides import build_channel_tool_profile_context_overrides
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.channels.ingress_journal import get_channel_ingress_journal_service
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.settings import Settings

PluginRunChatTurn = Callable[..., Awaitable[TurnResult]]


@dataclass(frozen=True, slots=True)
class PluginInboundMessage:
    """Normalized inbound text event emitted by a plugin channel runtime."""

    peer_id: str
    text: str
    event_key: str
    message_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    chat_kind: str = "private"
    observed_at: str | None = None


class PluginChannelIngressDispatcher:
    """Route plugin channel messages through AFKBOT's shared channel ingress path."""

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint: ChannelEndpointConfig,
        channel_delivery_service: ChannelDeliveryService | None = None,
        run_chat_turn_fn: PluginRunChatTurn = run_chat_turn,
    ) -> None:
        self._settings = settings
        self._endpoint = endpoint
        self._channel_delivery_service = channel_delivery_service or ChannelDeliveryService(
            settings
        )
        self._run_chat_turn = run_chat_turn_fn

    async def dispatch_text(
        self,
        message: PluginInboundMessage,
        *,
        reply: bool = True,
        require_binding_match: bool = False,
    ) -> TurnResult | None:
        """Run one inbound plugin message through routing, AgentLoop, and optional reply."""

        inbound = _normalize_inbound_message(message)
        if not inbound.peer_id:
            raise ValueError("Plugin inbound message requires peer_id")
        if not inbound.event_key:
            raise ValueError("Plugin inbound message requires event_key")
        if not inbound.text.strip():
            return None
        if not is_channel_message_allowed(
            policy=self._endpoint.access_policy,
            chat_kind=inbound.chat_kind,
            peer_id=inbound.peer_id,
            user_id=inbound.user_id,
        ):
            return None
        journal = get_channel_ingress_journal_service(self._settings)
        claimed = await journal.try_claim(
            endpoint_id=self._endpoint.endpoint_id,
            transport=self._endpoint.transport,
            event_key=inbound.event_key,
        )
        if not claimed:
            return None
        try:
            turn_result = await self._run_turn(
                inbound=inbound,
                require_binding_match=require_binding_match,
            )
            if reply:
                await self._reply_if_needed(turn_result=turn_result, inbound=inbound)
            return turn_result
        except Exception:
            await journal.release_claim(
                endpoint_id=self._endpoint.endpoint_id,
                event_key=inbound.event_key,
            )
            raise

    async def _run_turn(
        self,
        *,
        inbound: PluginInboundMessage,
        require_binding_match: bool,
    ) -> TurnResult:
        selectors = RoutingSelectors(
            transport=self._endpoint.transport,
            account_id=self._endpoint.account_id,
            peer_id=inbound.peer_id,
            thread_id=inbound.thread_id,
            user_id=inbound.user_id,
        )
        try:
            target = await resolve_runtime_target(
                settings=self._settings,
                explicit_profile_id=None,
                explicit_session_id=None,
                resolve_binding=True,
                require_binding_match=require_binding_match,
                selectors=selectors,
                default_profile_id=self._endpoint.profile_id,
                default_session_id=f"{self._endpoint.transport}:{inbound.peer_id}",
            )
        except ChannelBindingServiceError as exc:
            if exc.error_code != "channel_binding_no_match":
                raise
            target = await resolve_runtime_target(
                settings=self._settings,
                explicit_profile_id=None,
                explicit_session_id=None,
                resolve_binding=False,
                selectors=selectors,
                default_profile_id=self._endpoint.profile_id,
                default_session_id=f"{self._endpoint.transport}:{inbound.peer_id}",
            )
        context_overrides = merge_turn_context_overrides(
            build_routing_context_overrides(target=target, selectors=selectors),
            build_active_channel_context_overrides(
                endpoint=self._endpoint,
                peer_id=inbound.peer_id,
                thread_id=inbound.thread_id,
                user_id=inbound.user_id,
            ),
            build_channel_tool_profile_context_overrides(self._endpoint.tool_profile),
        )
        return await self._run_chat_turn(
            message=inbound.text,
            profile_id=target.profile_id,
            session_id=target.session_id,
            client_msg_id=_client_msg_id(endpoint_id=self._endpoint.endpoint_id, inbound=inbound),
            context_overrides=context_overrides,
        )

    async def _reply_if_needed(
        self,
        *,
        turn_result: TurnResult,
        inbound: PluginInboundMessage,
    ) -> None:
        if turn_result.envelope.action != "finalize":
            return
        if should_suppress_channel_reply(turn_result.envelope):
            return
        response_text = turn_result.envelope.message.strip()
        if not response_text:
            return
        try:
            await self._channel_delivery_service.deliver_text(
                profile_id=turn_result.profile_id,
                session_id=turn_result.session_id,
                run_id=turn_result.run_id,
                target=ChannelDeliveryTarget(
                    transport=self._endpoint.transport,
                    adapter_kind=self._endpoint.adapter_kind,
                    account_id=self._endpoint.account_id,
                    peer_id=inbound.peer_id,
                    thread_id=inbound.thread_id,
                    user_id=inbound.user_id,
                ),
                text=response_text,
                credential_profile_key=self._endpoint.credential_profile_key,
            )
        except ChannelDeliveryServiceError:
            raise


def _normalize_inbound_message(message: PluginInboundMessage) -> PluginInboundMessage:
    return PluginInboundMessage(
        peer_id=message.peer_id.strip(),
        text=message.text.strip(),
        event_key=message.event_key.strip(),
        message_id=None if message.message_id is None else message.message_id.strip() or None,
        thread_id=None if message.thread_id is None else message.thread_id.strip() or None,
        user_id=None if message.user_id is None else message.user_id.strip() or None,
        chat_kind=(message.chat_kind or "private").strip().lower() or "private",
        observed_at=message.observed_at or datetime.now(UTC).isoformat(),
    )


def _client_msg_id(*, endpoint_id: str, inbound: PluginInboundMessage) -> str:
    raw = "|".join(
        (
            endpoint_id,
            inbound.peer_id,
            inbound.thread_id or "",
            inbound.user_id or "",
            inbound.event_key,
            inbound.message_id or "",
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"channel:{endpoint_id}:{digest}"
