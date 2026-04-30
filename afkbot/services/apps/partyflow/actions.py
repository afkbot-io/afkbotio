"""PartyFlow app actions for unified `app.run` runtime."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from afkbot.services.apps.common import (
    AppCallContext,
    credentials_error_result,
    ensure_host_allowed,
    policy_error_result,
    resolve_credential_value,
)
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.credential_manifest import (
    ActionCredentialManifest,
    AppCredentialManifest,
    CredentialFieldManifest,
)
from afkbot.services.apps.params_validation import build_app_params_validation_error
from afkbot.services.apps.partyflow.http_api import (
    PartyFlowApiError,
    _get_me,
    _get_messages,
    _join_conversation,
    _send_message,
)
from afkbot.services.apps.registry import register_app
from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.policy import PolicyViolationError
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

_DEFAULT_BASE_URL = "https://api.partyflow.ru"
_ALLOWED_ACTIONS = frozenset(
    {"get_me", "join_conversation", "send_message", "get_messages", "read_channel_history"}
)
_ALLOWED_SKILLS = frozenset({"partyflow"})
_CREDENTIAL_MANIFEST = AppCredentialManifest(
    fields={
        "partyflow_bot_token": CredentialFieldManifest(
            slug="partyflow_bot_token",
            description="PartyFlow bot bearer token.",
        ),
        "partyflow_webhook_signing_secret": CredentialFieldManifest(
            slug="partyflow_webhook_signing_secret",
            description="PartyFlow outgoing webhook signing secret.",
            required_by_default=False,
        ),
    },
    actions={
        "get_me": ActionCredentialManifest(required=("partyflow_bot_token",)),
        "join_conversation": ActionCredentialManifest(required=("partyflow_bot_token",)),
        "send_message": ActionCredentialManifest(required=("partyflow_bot_token",)),
        "get_messages": ActionCredentialManifest(required=("partyflow_bot_token",)),
        "read_channel_history": ActionCredentialManifest(required=("partyflow_bot_token",)),
    },
)


class _BasePartyFlowParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default=_DEFAULT_BASE_URL, min_length=1, max_length=512)
    token_credential_name: str = Field(default="partyflow_bot_token", min_length=1, max_length=128)


class _GetMeParams(_BasePartyFlowParams):
    pass


class _JoinConversationParams(_BasePartyFlowParams):
    conversation_id: str = Field(min_length=1, max_length=128)


class _SendMessageParams(_BasePartyFlowParams):
    conversation_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    display_avatar_url: str | None = Field(default=None, min_length=1, max_length=2048)
    metadata_json: str | None = Field(default=None, max_length=32768)

    @field_validator("display_avatar_url")
    @classmethod
    def _validate_display_avatar_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if urlparse(value).scheme.lower() != "https":
            raise ValueError("display_avatar_url must use HTTPS")
        return value

    @field_validator("metadata_json")
    @classmethod
    def _validate_metadata_json(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("metadata_json must be a valid JSON string") from exc
        return value


class _GetMessagesParams(_BasePartyFlowParams):
    conversation_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=50, ge=1, le=100)
    before_msg_index: int | None = Field(default=None, ge=0)
    after_msg_index: int | None = Field(default=None, ge=0)
    around_msg_index: int | None = Field(default=None, ge=0)
    updated_since: str | None = Field(default=None, min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_single_cursor(self) -> "_GetMessagesParams":
        cursor_count = sum(
            item is not None
            for item in (self.before_msg_index, self.after_msg_index, self.around_msg_index)
        )
        if cursor_count > 1:
            raise ValueError(
                "only one of before_msg_index, after_msg_index, around_msg_index may be set"
            )
        return self


_ACTION_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    "get_me": _GetMeParams,
    "join_conversation": _JoinConversationParams,
    "send_message": _SendMessageParams,
    "get_messages": _GetMessagesParams,
    "read_channel_history": _GetMessagesParams,
}


@register_app(
    name="partyflow",
    allowed_skills=_ALLOWED_SKILLS,
    allowed_actions=_ALLOWED_ACTIONS,
    action_params_models=_ACTION_PARAMS_MODELS,
    credential_manifest=_CREDENTIAL_MANIFEST,
)
async def run_partyflow_action(
    settings: Settings,
    ctx: AppRuntimeContext,
    action: str,
    params: dict[str, object],
) -> ToolResult:
    """Dispatch PartyFlow app action by name."""

    normalized_action = action.strip().lower()
    call_context = AppCallContext(
        profile_id=ctx.profile_id,
        app_name="partyflow",
        action=normalized_action,
        profile_name=ctx.credential_profile_key,
    )
    try:
        if normalized_action == "get_me":
            get_me_params = _GetMeParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=get_me_params.token_credential_name,
            )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host=_host_from_base_url(get_me_params.base_url),
            )
            result = await _get_me(
                base_url=get_me_params.base_url,
                token=token,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=result)
        if normalized_action == "join_conversation":
            join_params = _JoinConversationParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=join_params.token_credential_name,
            )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host=_host_from_base_url(join_params.base_url),
            )
            result = await _join_conversation(
                base_url=join_params.base_url,
                token=token,
                conversation_id=join_params.conversation_id,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=result)
        if normalized_action == "send_message":
            send_params = _SendMessageParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=send_params.token_credential_name,
            )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host=_host_from_base_url(send_params.base_url),
            )
            result = await _send_message(
                base_url=send_params.base_url,
                token=token,
                conversation_id=send_params.conversation_id,
                content=send_params.content,
                thread_id=send_params.thread_id,
                display_name=send_params.display_name,
                display_avatar_url=send_params.display_avatar_url,
                metadata_json=send_params.metadata_json,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=result)
        if normalized_action in {"get_messages", "read_channel_history"}:
            get_messages_params = _GetMessagesParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=get_messages_params.token_credential_name,
            )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host=_host_from_base_url(get_messages_params.base_url),
            )
            result = await _get_messages(
                base_url=get_messages_params.base_url,
                token=token,
                conversation_id=get_messages_params.conversation_id,
                limit=get_messages_params.limit,
                before_msg_index=get_messages_params.before_msg_index,
                after_msg_index=get_messages_params.after_msg_index,
                around_msg_index=get_messages_params.around_msg_index,
                updated_since=get_messages_params.updated_since,
                thread_id=get_messages_params.thread_id,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=result)
        return ToolResult.error(
            error_code="app_action_not_supported",
            reason=f"Unsupported partyflow action: {normalized_action}",
        )
    except ValidationError as exc:
        return build_app_params_validation_error(
            app_name="partyflow",
            action=normalized_action,
            model=_model_for_action(normalized_action),
            exc=exc,
        )
    except CredentialsServiceError as exc:
        error_code, reason, metadata = credentials_error_result(exc=exc, context=call_context)
        return ToolResult.error(error_code=error_code, reason=reason, metadata=metadata)
    except PolicyViolationError as exc:
        error_code, reason = policy_error_result(exc)
        return ToolResult.error(error_code=error_code, reason=reason)
    except PartyFlowApiError as exc:
        return ToolResult.error(
            error_code=exc.error_code,
            reason=exc.reason,
            metadata=exc.metadata,
        )
    except Exception as exc:
        return ToolResult.error(
            error_code="app_run_failed",
            reason=f"{exc.__class__.__name__}: {exc}",
        )


def _host_from_base_url(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if not host:
        raise ValueError("PartyFlow base_url must include a valid host")
    return host


def _model_for_action(action: str) -> type[BaseModel]:
    return _ACTION_PARAMS_MODELS.get(action, _GetMeParams)
