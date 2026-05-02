"""Tool plugin for current-channel message history reads."""

from __future__ import annotations

from pydantic import Field, model_validator

from afkbot.services.apps.partyflow.http_api import PartyFlowApiError, _get_messages
from afkbot.services.channels.active_context import active_channel_context_from_trusted
from afkbot.services.channels.endpoint_contracts import PartyFlowWebhookEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings

_PARTYFLOW_BASE_URL = "https://api.partyflow.ru"
_PARTYFLOW_BOT_TOKEN = "partyflow_bot_token"


class ChannelHistoryListParams(ToolParameters):
    """Parameters for channel.history.list."""

    endpoint_id: str | None = Field(default=None, min_length=1, max_length=64)
    transport: str | None = Field(default=None, min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)
    peer_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=50, ge=1, le=100)
    before_msg_index: int | None = Field(default=None, ge=0)
    after_msg_index: int | None = Field(default=None, ge=0)
    around_msg_index: int | None = Field(default=None, ge=0)
    updated_since: str | None = Field(default=None, min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_single_cursor(self) -> "ChannelHistoryListParams":
        cursor_count = sum(
            item is not None
            for item in (self.before_msg_index, self.after_msg_index, self.around_msg_index)
        )
        if cursor_count > 1:
            raise ValueError(
                "only one of before_msg_index, after_msg_index, around_msg_index may be set"
            )
        return self

    @property
    def requested_conversation_id(self) -> str | None:
        """Return the first explicit conversation/chat alias."""

        for value in (self.conversation_id, self.chat_id, self.peer_id):
            normalized = (value or "").strip()
            if normalized:
                return normalized
        return None


class ChannelHistoryListTool(ToolBase):
    """Read message history through the active channel's own API."""

    name = "channel.history.list"
    description = (
        "Read message history for the current external channel. PartyFlow is supported now via the "
        "current webhook endpoint and Bot REST API. In an inbound PartyFlow turn, omit endpoint_id "
        "and conversation_id to read the current conversation; optional limit, cursor, updated_since, "
        "and thread_id narrow the read. Outside an active channel turn, endpoint_id and conversation_id "
        "are required. This tool never exposes generic app.run."
    )
    parameters_model = ChannelHistoryListParams
    parallel_execution_safe = True

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint_service: ChannelEndpointService | None = None,
    ) -> None:
        self._settings = settings
        self._endpoint_service = endpoint_service or get_channel_endpoint_service(settings)

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = self._prepare_params(ctx=ctx, params=params, expected=ChannelHistoryListParams)
        if isinstance(payload, ToolResult):
            return payload
        active_context = active_channel_context_from_trusted(ctx.trusted_runtime_context)
        endpoint_id = payload.endpoint_id or (
            active_context.endpoint_id if active_context is not None else None
        )
        if endpoint_id is None:
            return ToolResult.error(
                error_code="channel_history_endpoint_required",
                reason=(
                    "channel.history.list requires endpoint_id outside an active channel turn."
                ),
            )
        if active_context is not None and endpoint_id != active_context.endpoint_id:
            return ToolResult.error(
                error_code="channel_history_endpoint_not_active",
                reason="channel.history.list may only read the active inbound channel endpoint.",
                metadata={
                    "requested_endpoint_id": endpoint_id,
                    "active_endpoint_id": active_context.endpoint_id,
                },
            )

        endpoint_or_error = await self._load_partyflow_endpoint(ctx=ctx, endpoint_id=endpoint_id)
        if isinstance(endpoint_or_error, ToolResult):
            return endpoint_or_error
        endpoint = endpoint_or_error
        requested_transport = (payload.transport or endpoint.transport).strip().lower()
        if requested_transport != endpoint.transport:
            return ToolResult.error(
                error_code="channel_history_transport_mismatch",
                reason="channel.history.list transport does not match the selected endpoint.",
                metadata={
                    "endpoint_id": endpoint.endpoint_id,
                    "endpoint_transport": endpoint.transport,
                    "requested_transport": requested_transport,
                },
            )

        conversation_id = self._resolve_conversation_id(
            payload=payload,
            active_peer_id=None if active_context is None else active_context.peer_id,
        )
        if isinstance(conversation_id, ToolResult):
            return conversation_id
        if (
            active_context is not None
            and active_context.peer_id is not None
            and conversation_id != active_context.peer_id
        ):
            return ToolResult.error(
                error_code="channel_history_conversation_not_active",
                reason="channel.history.list may only read the active inbound PartyFlow conversation.",
                metadata={
                    "requested_conversation_id": conversation_id,
                    "active_conversation_id": active_context.peer_id,
                },
            )
        thread_id = payload.thread_id or (None if active_context is None else active_context.thread_id)
        if (
            active_context is not None
            and active_context.thread_id is not None
            and thread_id != active_context.thread_id
        ):
            return ToolResult.error(
                error_code="channel_history_thread_not_active",
                reason="channel.history.list may only read the active PartyFlow thread from this turn.",
                metadata={
                    "requested_thread_id": thread_id,
                    "active_thread_id": active_context.thread_id,
                },
            )

        try:
            token = await get_credentials_service(
                self._settings
            ).resolve_plaintext_for_app_tool(
                profile_id=ctx.profile_id,
                tool_name="app.run",
                integration_name="partyflow",
                credential_profile_key=endpoint.credential_profile_key,
                credential_name=_PARTYFLOW_BOT_TOKEN,
            )
            result = await _get_messages(
                base_url=_PARTYFLOW_BASE_URL,
                token=token,
                conversation_id=conversation_id,
                limit=payload.limit,
                before_msg_index=payload.before_msg_index,
                after_msg_index=payload.after_msg_index,
                around_msg_index=payload.around_msg_index,
                updated_since=payload.updated_since,
                thread_id=thread_id,
                timeout_sec=payload.timeout_sec,
            )
        except CredentialsServiceError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata={
                    "endpoint_id": endpoint.endpoint_id,
                    "credential_profile_key": endpoint.credential_profile_key,
                },
            )
        except PartyFlowApiError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata={"endpoint_id": endpoint.endpoint_id, **exc.metadata},
            )
        return ToolResult(
            ok=True,
            payload={
                **result,
                "transport": endpoint.transport,
                "endpoint_id": endpoint.endpoint_id,
                "conversation_id": conversation_id,
                "thread_id": thread_id,
            },
        )

    async def _load_partyflow_endpoint(
        self,
        *,
        ctx: ToolContext,
        endpoint_id: str,
    ) -> PartyFlowWebhookEndpointConfig | ToolResult:
        try:
            endpoint = await self._endpoint_service.get(endpoint_id=endpoint_id)
        except ChannelEndpointServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        if endpoint.profile_id != ctx.profile_id:
            return ToolResult.error(
                error_code="channel_history_endpoint_not_in_profile",
                reason="channel.history.list endpoint does not belong to the active profile.",
                metadata={"endpoint_id": endpoint.endpoint_id, "profile_id": endpoint.profile_id},
            )
        if endpoint.transport != "partyflow":
            return ToolResult.error(
                error_code="channel_history_transport_not_supported",
                reason="channel.history.list currently supports PartyFlow channel history only.",
                metadata={"endpoint_id": endpoint.endpoint_id, "transport": endpoint.transport},
            )
        if not endpoint.enabled:
            return ToolResult.error(
                error_code="channel_history_endpoint_disabled",
                reason="channel.history.list endpoint is disabled.",
                metadata={"endpoint_id": endpoint.endpoint_id},
            )
        return PartyFlowWebhookEndpointConfig.model_validate(endpoint.model_dump())

    @staticmethod
    def _resolve_conversation_id(
        *,
        payload: ChannelHistoryListParams,
        active_peer_id: str | None,
    ) -> str | ToolResult:
        requested = payload.requested_conversation_id
        if requested:
            return requested
        if active_peer_id:
            return active_peer_id
        return ToolResult.error(
            error_code="channel_history_conversation_required",
            reason=(
                "channel.history.list requires conversation_id/chat_id outside an active "
                "PartyFlow conversation."
            ),
        )


def create_tool(settings: Settings) -> ToolBase:
    """Create channel.history.list tool instance."""

    return ChannelHistoryListTool(settings=settings)
