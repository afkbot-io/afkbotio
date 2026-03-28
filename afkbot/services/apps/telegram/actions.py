"""Telegram app actions for unified `app.run` runtime."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from afkbot.services.apps.common import (
    AppCallContext,
    credentials_error_result,
    ensure_host_allowed,
    policy_error_result,
    resolve_credential_value,
)
from afkbot.services.apps.credential_manifest import (
    ActionCredentialManifest,
    AppCredentialManifest,
    CredentialFieldManifest,
)
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.params_validation import build_app_params_validation_error
from afkbot.services.apps.registry import register_app
from afkbot.services.apps.telegram.http_api import (
    _post_ban_chat_member,
    _post_get_me,
    _post_get_updates,
    _post_send_chat_action,
    _post_send_media,
    _post_send_message,
    _post_unban_chat_member,
)
from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.policy import PolicyViolationError
from afkbot.services.telegram_text import split_telegram_text
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

_ALLOWED_ACTIONS = frozenset(
    {
        "send_message",
        "send_photo",
        "send_document",
        "send_chat_action",
        "get_me",
        "get_updates",
        "ban_chat_member",
        "unban_chat_member",
    }
)
_ALLOWED_SKILLS = frozenset({"telegram"})
_CREDENTIAL_MANIFEST = AppCredentialManifest(
    fields={
        "telegram_token": CredentialFieldManifest(
            slug="telegram_token",
            description="Telegram Bot API token.",
        ),
        "telegram_chat_id": CredentialFieldManifest(
            slug="telegram_chat_id",
            description="Default Telegram chat or channel id.",
            required_by_default=False,
        ),
    },
    actions={
        "send_message": ActionCredentialManifest(
            required=("telegram_token",),
            optional=("telegram_chat_id",),
        ),
        "send_photo": ActionCredentialManifest(
            required=("telegram_token",),
            optional=("telegram_chat_id",),
        ),
        "send_document": ActionCredentialManifest(
            required=("telegram_token",),
            optional=("telegram_chat_id",),
        ),
        "send_chat_action": ActionCredentialManifest(
            required=("telegram_token",),
            optional=("telegram_chat_id",),
        ),
        "get_me": ActionCredentialManifest(required=("telegram_token",)),
        "get_updates": ActionCredentialManifest(required=("telegram_token",)),
        "ban_chat_member": ActionCredentialManifest(
            required=("telegram_token",),
            optional=("telegram_chat_id",),
        ),
        "unban_chat_member": ActionCredentialManifest(
            required=("telegram_token",),
            optional=("telegram_chat_id",),
        ),
    },
)


class _SendMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200_000)
    chat_id: str | None = Field(default=None, max_length=128)
    message_thread_id: int | None = Field(default=None, ge=1)
    parse_mode: str | None = Field(default=None, max_length=32)
    disable_web_page_preview: bool = False
    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    chat_id_credential_name: str = Field(
        default="telegram_chat_id",
        min_length=1,
        max_length=128,
    )


class _SendPhotoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo: str = Field(min_length=1, max_length=4096)
    caption: str | None = Field(default=None, max_length=1024)
    chat_id: str | None = Field(default=None, max_length=128)
    message_thread_id: int | None = Field(default=None, ge=1)
    parse_mode: str | None = Field(default=None, max_length=32)
    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    chat_id_credential_name: str = Field(
        default="telegram_chat_id",
        min_length=1,
        max_length=128,
    )


class _SendDocumentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str = Field(min_length=1, max_length=4096)
    caption: str | None = Field(default=None, max_length=1024)
    chat_id: str | None = Field(default=None, max_length=128)
    message_thread_id: int | None = Field(default=None, ge=1)
    parse_mode: str | None = Field(default=None, max_length=32)
    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    chat_id_credential_name: str = Field(
        default="telegram_chat_id",
        min_length=1,
        max_length=128,
    )


class _GetMeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)


class _GetUpdatesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=100)
    timeout: int = Field(default=0, ge=0, le=50)
    offset: int | None = Field(default=None, ge=0)


class _SendChatActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(default="typing", min_length=1, max_length=32)
    chat_id: str | None = Field(default=None, max_length=128)
    message_thread_id: int | None = Field(default=None, ge=1)
    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    chat_id_credential_name: str = Field(
        default="telegram_chat_id",
        min_length=1,
        max_length=128,
    )


class _BanChatMemberParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(ge=1)
    chat_id: str | None = Field(default=None, max_length=128)
    revoke_messages: bool = False
    until_date: int | None = Field(default=None, ge=0)
    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    chat_id_credential_name: str = Field(
        default="telegram_chat_id",
        min_length=1,
        max_length=128,
    )


class _UnbanChatMemberParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(ge=1)
    chat_id: str | None = Field(default=None, max_length=128)
    only_if_banned: bool = False
    token_credential_name: str = Field(default="telegram_token", min_length=1, max_length=128)
    chat_id_credential_name: str = Field(
        default="telegram_chat_id",
        min_length=1,
        max_length=128,
    )


_ACTION_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    "send_message": _SendMessageParams,
    "send_photo": _SendPhotoParams,
    "send_document": _SendDocumentParams,
    "send_chat_action": _SendChatActionParams,
    "get_me": _GetMeParams,
    "get_updates": _GetUpdatesParams,
    "ban_chat_member": _BanChatMemberParams,
    "unban_chat_member": _UnbanChatMemberParams,
}


@register_app(
    name="telegram",
    allowed_skills=_ALLOWED_SKILLS,
    allowed_actions=_ALLOWED_ACTIONS,
    action_params_models=_ACTION_PARAMS_MODELS,
    credential_manifest=_CREDENTIAL_MANIFEST,
)
async def run_telegram_action(
    settings: Settings,
    ctx: AppRuntimeContext,
    action: str,
    params: dict[str, object],
) -> ToolResult:
    """Dispatch Telegram app action by name."""

    normalized_action = action.strip().lower()
    call_context = AppCallContext(
        profile_id=ctx.profile_id,
        app_name="telegram",
        action=normalized_action,
        profile_name=ctx.credential_profile_key,
    )

    try:
        if normalized_action == "send_message":
            send_payload = _SendMessageParams.model_validate(params)
            message_parts = split_telegram_text(send_payload.text)
            if not message_parts:
                raise ValueError("Telegram send_message text must contain non-whitespace content")
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=send_payload.token_credential_name,
            )
            chat_id = send_payload.chat_id
            if not chat_id:
                chat_id = await resolve_credential_value(
                    settings=settings,
                    context=call_context,
                    credential_slug=send_payload.chat_id_credential_name,
                )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            responses: list[dict[str, object]] = []
            for message_part in message_parts:
                responses.append(
                    await _post_send_message(
                        token=token,
                        chat_id=chat_id,
                        text=message_part,
                        message_thread_id=send_payload.message_thread_id,
                        parse_mode=send_payload.parse_mode,
                        disable_web_page_preview=send_payload.disable_web_page_preview,
                        timeout_sec=ctx.timeout_sec,
                    )
                )
            return ToolResult(ok=True, payload=_build_chunked_response(action="send_message", responses=responses))

        if normalized_action == "send_photo":
            photo_payload = _SendPhotoParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=photo_payload.token_credential_name,
            )
            chat_id = photo_payload.chat_id
            if not chat_id:
                chat_id = await resolve_credential_value(
                    settings=settings,
                    context=call_context,
                    credential_slug=photo_payload.chat_id_credential_name,
                )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_send_media(
                settings=settings,
                profile_id=ctx.profile_id,
                token=token,
                chat_id=chat_id,
                action="send_photo",
                field_name="photo",
                media_value=photo_payload.photo,
                caption=photo_payload.caption,
                message_thread_id=photo_payload.message_thread_id,
                parse_mode=photo_payload.parse_mode,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=response)

        if normalized_action == "send_document":
            document_payload = _SendDocumentParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=document_payload.token_credential_name,
            )
            chat_id = document_payload.chat_id
            if not chat_id:
                chat_id = await resolve_credential_value(
                    settings=settings,
                    context=call_context,
                    credential_slug=document_payload.chat_id_credential_name,
                )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_send_media(
                settings=settings,
                profile_id=ctx.profile_id,
                token=token,
                chat_id=chat_id,
                action="send_document",
                field_name="document",
                media_value=document_payload.document,
                caption=document_payload.caption,
                message_thread_id=document_payload.message_thread_id,
                parse_mode=document_payload.parse_mode,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=response)

        if normalized_action == "send_chat_action":
            action_payload = _SendChatActionParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=action_payload.token_credential_name,
            )
            chat_id = action_payload.chat_id
            if not chat_id:
                chat_id = await resolve_credential_value(
                    settings=settings,
                    context=call_context,
                    credential_slug=action_payload.chat_id_credential_name,
                )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_send_chat_action(
                token=token,
                chat_id=chat_id,
                action=action_payload.action,
                message_thread_id=action_payload.message_thread_id,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=response)

        if normalized_action == "get_me":
            get_me_payload = _GetMeParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=get_me_payload.token_credential_name,
            )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_get_me(token=token, timeout_sec=ctx.timeout_sec)
            return ToolResult(ok=True, payload=response)

        if normalized_action == "get_updates":
            get_updates_payload = _GetUpdatesParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=get_updates_payload.token_credential_name,
            )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_get_updates(
                token=token,
                limit=get_updates_payload.limit,
                timeout=get_updates_payload.timeout,
                offset=get_updates_payload.offset,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=response)

        if normalized_action == "ban_chat_member":
            ban_payload = _BanChatMemberParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=ban_payload.token_credential_name,
            )
            chat_id = ban_payload.chat_id
            if not chat_id:
                chat_id = await resolve_credential_value(
                    settings=settings,
                    context=call_context,
                    credential_slug=ban_payload.chat_id_credential_name,
                )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_ban_chat_member(
                token=token,
                chat_id=chat_id,
                user_id=ban_payload.user_id,
                revoke_messages=ban_payload.revoke_messages,
                until_date=ban_payload.until_date,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=response)

        if normalized_action == "unban_chat_member":
            unban_payload = _UnbanChatMemberParams.model_validate(params)
            token = await resolve_credential_value(
                settings=settings,
                context=call_context,
                credential_slug=unban_payload.token_credential_name,
            )
            chat_id = unban_payload.chat_id
            if not chat_id:
                chat_id = await resolve_credential_value(
                    settings=settings,
                    context=call_context,
                    credential_slug=unban_payload.chat_id_credential_name,
                )
            await ensure_host_allowed(
                settings=settings,
                context=call_context,
                host="api.telegram.org",
            )
            response = await _post_unban_chat_member(
                token=token,
                chat_id=chat_id,
                user_id=unban_payload.user_id,
                only_if_banned=unban_payload.only_if_banned,
                timeout_sec=ctx.timeout_sec,
            )
            return ToolResult(ok=True, payload=response)

        return ToolResult.error(
            error_code="app_action_not_supported",
            reason=f"Unsupported telegram action: {action}",
        )
    except CredentialsServiceError as exc:
        error_code, reason, metadata = credentials_error_result(exc=exc, context=call_context)
        return ToolResult.error(error_code=error_code, reason=reason, metadata=metadata)
    except PolicyViolationError as exc:
        error_code, reason = policy_error_result(exc)
        return ToolResult.error(error_code=error_code, reason=reason)
    except ValidationError as exc:
        return build_app_params_validation_error(
            app_name="telegram",
            action=normalized_action,
            model=_model_for_action(normalized_action),
            exc=exc,
        )
    except ValueError as exc:
        return ToolResult.error(error_code="app_run_invalid", reason=str(exc))
    except TimeoutError:
        return ToolResult.error(
            error_code="app_run_failed",
            reason=f"Telegram action timed out after {ctx.timeout_sec} seconds",
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return ToolResult.error(
            error_code="app_run_failed",
            reason=f"{exc.__class__.__name__}: {exc}",
        )


def _model_for_action(action: str) -> type[BaseModel]:
    return _ACTION_PARAMS_MODELS.get(action, _GetUpdatesParams)


def _build_chunked_response(
    *,
    action: str,
    responses: list[dict[str, object]],
) -> dict[str, object]:
    if len(responses) == 1:
        return responses[0]
    return {
        "ok": True,
        "action": action,
        "chunk_count": len(responses),
        "chunks": responses,
    }
