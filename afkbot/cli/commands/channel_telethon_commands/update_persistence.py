"""Persistence helpers for Telethon update CLI flows."""

from __future__ import annotations

import asyncio

from afkbot.cli.commands.channel_prompt_support import resolve_channel_text
from afkbot.cli.presentation.setup_prompts import PromptLanguage
from afkbot.cli.commands.channel_shared import (
    put_access_policy_bindings,
    resolve_binding_update_inputs,
)
from afkbot.cli.commands.channel_telethon_commands.common import split_csv_patterns
from afkbot.cli.commands.channel_telethon_commands.legacy import get_legacy_channel_endpoint_service
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channels.endpoint_contracts import (
    ChannelAccessPolicy,
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
    TelethonGroupInvocationMode,
    TelethonReplyMode,
    TelethonUserEndpointConfig,
    TelethonWatcherConfig,
)
from afkbot.services.channels.tool_profiles import ChannelToolProfile
from afkbot.settings import Settings


def save_updated_telethon_channel(
    *,
    settings: Settings,
    current: TelethonUserEndpointConfig,
    profile_id: str,
    credential_profile_key: str | None,
    account_id: str | None,
    reply_mode: TelethonReplyMode,
    tool_profile: ChannelToolProfile,
    access_policy: ChannelAccessPolicy,
    reply_blocked_chat_patterns: str | None,
    reply_allowed_chat_patterns: str | None,
    group_invocation_mode: TelethonGroupInvocationMode,
    process_self_commands: bool,
    command_prefix: str,
    ingress_batch: ChannelIngressBatchConfig,
    reply_humanization: ChannelReplyHumanizationConfig,
    mark_read_before_reply: bool,
    watcher: TelethonWatcherConfig,
    prompt_language: PromptLanguage,
    sync_binding: bool,
    session_policy: SessionPolicy | None,
    prompt_overlay: str | None,
    priority: int | None,
) -> TelethonUserEndpointConfig:
    """Persist one updated Telethon endpoint and optionally sync its binding."""

    endpoint = TelethonUserEndpointConfig(
        endpoint_id=current.endpoint_id,
        profile_id=profile_id,
        credential_profile_key=resolve_channel_text(
            value=credential_profile_key,
            interactive=False,
            prompt_en="Credential profile",
            prompt_ru="Профиль учётных данных",
            default=current.credential_profile_key or current.endpoint_id,
            lang=prompt_language,
            normalize_lower=True,
        ),
        account_id=resolve_channel_text(
            value=account_id,
            interactive=False,
            prompt_en="Account id",
            prompt_ru="ID аккаунта",
            default=current.account_id,
            lang=prompt_language,
            normalize_lower=True,
        ),
        enabled=current.enabled,
        reply_mode=reply_mode,
        tool_profile=tool_profile,
        access_policy=access_policy,
        reply_blocked_chat_patterns=(
            current.reply_blocked_chat_patterns
            if reply_blocked_chat_patterns is None
            else split_csv_patterns(reply_blocked_chat_patterns)
        ),
        reply_allowed_chat_patterns=(
            current.reply_allowed_chat_patterns
            if reply_allowed_chat_patterns is None
            else split_csv_patterns(reply_allowed_chat_patterns)
        ),
        group_invocation_mode=group_invocation_mode,
        process_self_commands=process_self_commands,
        command_prefix=command_prefix,
        ingress_batch=ingress_batch,
        reply_humanization=reply_humanization,
        mark_read_before_reply=mark_read_before_reply,
        watcher=watcher,
    )
    saved = TelethonUserEndpointConfig.model_validate(
        asyncio.run(get_legacy_channel_endpoint_service(settings).update(endpoint)).model_dump()
    )
    if sync_binding:
        resolved_binding_inputs = resolve_binding_update_inputs(
            settings=settings,
            binding_id=saved.endpoint_id,
            session_policy=session_policy,
            session_policy_default="per-chat",
            priority=priority,
            prompt_overlay=prompt_overlay,
        )
        put_access_policy_bindings(
            settings=settings,
            endpoint_id=saved.endpoint_id,
            transport="telegram_user",
            profile_id=saved.profile_id,
            session_policy=resolved_binding_inputs.session_policy,
            priority=resolved_binding_inputs.priority,
            enabled=saved.enabled,
            account_id=saved.account_id,
            prompt_overlay=resolved_binding_inputs.prompt_overlay,
            access_policy=saved.access_policy,
            replace_existing=True,
        )
    return saved


__all__ = ["save_updated_telethon_channel"]
