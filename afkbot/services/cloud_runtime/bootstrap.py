"""Managed Cloud runtime bootstrap from control-plane manifest files."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from afkbot.services.automations import AutomationsServiceError, get_automations_service
from afkbot.services.channel_routing import (
    ChannelBindingRule,
    SessionPolicy,
    get_channel_binding_service,
)
from afkbot.services.channels.endpoint_contracts import (
    ChannelAccessPolicy,
    PartyFlowPollingEndpointConfig,
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_runtime_config_service
from afkbot.services.profile_runtime.service import ProfileServiceError, get_profile_service
from afkbot.settings import Settings

_APP_TOOL_NAME = "app.run"


async def apply_managed_cloud_manifest(*, settings: Settings, profile_id: str) -> None:
    """Apply Cloud control-plane files to AFKBOT runtime storage.

    :param settings: Runtime settings already resolved from container env.
    :param profile_id: Active runtime profile id.
    :return: None.
    """

    if not settings.cloud_gateway_enabled:
        return
    manifest = _load_manifest(settings=settings, profile_id=profile_id)
    runtime_config = get_profile_runtime_config_service(settings).load(profile_id)
    if runtime_config is None:
        return
    await _ensure_profile(settings=settings, profile_id=profile_id, runtime_config=runtime_config)
    await _apply_channels(settings=settings, profile_id=profile_id, manifest=manifest)
    await _apply_automations(settings=settings, profile_id=profile_id, manifest=manifest)


def _load_manifest(*, settings: Settings, profile_id: str) -> dict[str, Any]:
    manifest_path = (
        get_profile_runtime_config_service(settings).system_dir(profile_id) / "cloud_manifest.json"
    )
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


async def _ensure_profile(
    *,
    settings: Settings,
    profile_id: str,
    runtime_config: ProfileRuntimeConfig,
) -> None:
    profile_service = get_profile_service(settings)
    policy_capabilities = _policy_capabilities(runtime_config)
    policy_allowed_directories = (str(Path(settings.root_dir).resolve()),)
    try:
        if profile_id == "default":
            await profile_service.bootstrap_default(
                runtime_config=runtime_config,
                runtime_secrets=None,
                policy_enabled=True,
                policy_preset="medium",
                policy_capabilities=policy_capabilities,
                policy_file_access_mode="profile_shell",
                policy_allowed_directories=policy_allowed_directories,
                policy_shell_sandbox_mode="cloud",
                policy_network_allowlist=("*",),
            )
        else:
            await profile_service.create(
                profile_id=profile_id,
                name=_profile_title(profile_id),
                runtime_config=runtime_config,
                runtime_secrets=None,
                policy_enabled=True,
                policy_preset="medium",
                policy_capabilities=policy_capabilities,
                policy_file_access_mode="profile_shell",
                policy_allowed_directories=policy_allowed_directories,
                policy_shell_sandbox_mode="cloud",
                policy_network_allowlist=("*",),
            )
    except ProfileServiceError as exc:
        if exc.error_code != "profile_exists":
            raise
        await profile_service.update(
            profile_id=profile_id,
            name=_profile_title(profile_id),
            runtime_config=runtime_config,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=policy_capabilities,
            policy_file_access_mode="profile_shell",
            policy_allowed_directories=policy_allowed_directories,
            policy_shell_sandbox_mode="cloud",
            policy_network_allowlist=("*",),
        )


async def _apply_channels(
    *,
    settings: Settings,
    profile_id: str,
    manifest: Mapping[str, Any],
) -> None:
    raw_channels = manifest.get("channels")
    if not isinstance(raw_channels, list):
        return
    for raw in raw_channels:
        if not isinstance(raw, Mapping) or raw.get("enabled") is False:
            continue
        endpoint = _build_channel_endpoint(profile_id=profile_id, raw=raw)
        if endpoint is None:
            continue
        await _upsert_channel_credentials(settings=settings, profile_id=profile_id, raw=raw)
        endpoint_service = get_channel_endpoint_service(settings)
        try:
            await endpoint_service.create(endpoint)
        except ChannelEndpointServiceError as exc:
            if exc.error_code != "channel_endpoint_exists":
                raise
            await endpoint_service.update(endpoint)
        if raw.get("create_binding", True) is not False:
            await get_channel_binding_service(settings).put(
                ChannelBindingRule(
                    binding_id=endpoint.endpoint_id,
                    transport=endpoint.transport,
                    profile_id=endpoint.profile_id,
                    session_policy=_session_policy(raw),
                    enabled=endpoint.enabled,
                    account_id=endpoint.account_id,
                    prompt_overlay=_optional_string(raw.get("prompt_overlay")),
                )
            )


def _build_channel_endpoint(
    *,
    profile_id: str,
    raw: Mapping[str, Any],
) -> TelegramPollingEndpointConfig | TelethonUserEndpointConfig | PartyFlowPollingEndpointConfig | None:
    kind = str(raw.get("kind") or raw.get("transport") or "").strip().lower()
    endpoint_id = _endpoint_id(raw)
    credential_profile_key = _credential_profile_key(raw, endpoint_id=endpoint_id)
    account_id = _account_id(raw, endpoint_id=endpoint_id)
    access_policy = _access_policy(raw)
    common: dict[str, object] = {
        "endpoint_id": endpoint_id,
        "profile_id": profile_id,
        "credential_profile_key": credential_profile_key,
        "account_id": account_id,
        "enabled": raw.get("enabled") is not False,
        "tool_profile": raw.get("tool_profile") or "messaging_safe",
        "access_policy": access_policy,
    }
    if kind in {"telegram_bot", "telegram"}:
        return TelegramPollingEndpointConfig.model_validate(
            common
            | {
                "group_trigger_mode": str(
                    raw.get("trigger_mode") or raw.get("group_trigger_mode") or "mention_or_reply"
                )
            }
        )
    if kind in {"telethon", "telegram_user"}:
        return TelethonUserEndpointConfig.model_validate(
            common
            | {
                "reply_mode": str(raw.get("reply_mode") or "same_chat"),
                "group_invocation_mode": str(raw.get("group_invocation_mode") or "reply_or_command"),
                "command_prefix": str(raw.get("command_prefix") or ".afk"),
                "process_self_commands": bool(raw.get("process_self_commands") or False),
                "mark_read_before_reply": raw.get("mark_read_before_reply") is not False,
            }
        )
    if kind == "partyflow":
        return PartyFlowPollingEndpointConfig.model_validate(
            common
            | {
                "trigger_mode": str(raw.get("trigger_mode") or "mention"),
                "trigger_keywords": tuple(_string_list(raw.get("trigger_keywords"))),
                "reply_mode": str(raw.get("reply_mode") or "same_conversation"),
            }
        )
    return None


async def _upsert_channel_credentials(
    *,
    settings: Settings,
    profile_id: str,
    raw: Mapping[str, Any],
) -> None:
    secret_refs = raw.get("secret_refs")
    if not isinstance(secret_refs, Mapping):
        return
    kind = str(raw.get("kind") or raw.get("transport") or "").strip().lower()
    endpoint_id = _endpoint_id(raw)
    credential_profile_key = _credential_profile_key(raw, endpoint_id=endpoint_id)
    if kind in {"telegram_bot", "telegram"}:
        await _create_credential(
            settings=settings,
            profile_id=profile_id,
            integration_name="telegram",
            credential_profile_key=credential_profile_key,
            credential_name="telegram_token",
            secret_value=_env_secret(secret_refs, "TELEGRAM_BOT_TOKEN"),
        )
        await _create_credential(
            settings=settings,
            profile_id=profile_id,
            integration_name="telegram",
            credential_profile_key=credential_profile_key,
            credential_name="telegram_chat_id",
            secret_value=_env_secret(secret_refs, "TELEGRAM_CHAT_ID"),
        )
    elif kind in {"telethon", "telegram_user"}:
        for env_name, credential_name in (
            ("TELEGRAM_API_ID", "api_id"),
            ("TELEGRAM_API_HASH", "api_hash"),
            ("TELEGRAM_PHONE", "phone"),
            ("TELEGRAM_SESSION", "session_string"),
        ):
            await _create_credential(
                settings=settings,
                profile_id=profile_id,
                integration_name="telethon",
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                secret_value=_env_secret(secret_refs, env_name),
            )
    elif kind == "partyflow":
        await _create_credential(
            settings=settings,
            profile_id=profile_id,
            integration_name="partyflow",
            credential_profile_key=credential_profile_key,
            credential_name="partyflow_bot_token",
            secret_value=_env_secret(secret_refs, "PARTYFLOW_BOT_TOKEN"),
        )


async def _create_credential(
    *,
    settings: Settings,
    profile_id: str,
    integration_name: str,
    credential_profile_key: str,
    credential_name: str,
    secret_value: str | None,
) -> None:
    if not secret_value:
        return
    try:
        await get_credentials_service(settings).create(
            profile_id=profile_id,
            tool_name=_APP_TOOL_NAME,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
            replace_existing=True,
        )
    except CredentialsServiceError as exc:
        if exc.error_code != "credentials_conflict":
            raise
        await get_credentials_service(settings).update(
            profile_id=profile_id,
            tool_name=_APP_TOOL_NAME,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
        )


async def _apply_automations(
    *,
    settings: Settings,
    profile_id: str,
    manifest: Mapping[str, Any],
) -> None:
    raw_automations = manifest.get("automations")
    if not isinstance(raw_automations, list):
        return
    service = get_automations_service(settings)
    existing = {
        item.name: item
        for item in await service.list(profile_id=profile_id, include_deleted=False)
    }
    for raw in raw_automations:
        if not isinstance(raw, Mapping) or raw.get("enabled") is False:
            continue
        name = _optional_string(raw.get("name")) or _optional_string(raw.get("id")) or "Automation"
        prompt = _optional_string(raw.get("prompt")) or "Run this automation."
        trigger_type = str(raw.get("trigger_type") or "cron").strip().lower()
        try:
            if name in existing:
                item = existing[name]
                await service.update(
                    profile_id=profile_id,
                    automation_id=item.id,
                    name=name,
                    prompt=prompt,
                    status="active",
                    cron_expr=_optional_string(raw.get("cron_expr")) if item.cron else None,
                    timezone_name=_optional_string(raw.get("timezone_name")) if item.cron else None,
                )
            elif trigger_type == "webhook":
                await service.create_webhook(
                    profile_id=profile_id,
                    name=name,
                    prompt=prompt,
                    webhook_token=_optional_string(raw.get("webhook_token")),
                )
            else:
                await service.create_cron(
                    profile_id=profile_id,
                    name=name,
                    prompt=prompt,
                    cron_expr=_optional_string(raw.get("cron_expr")) or "0 9 * * *",
                    timezone_name=_optional_string(raw.get("timezone_name")) or "UTC",
                )
        except AutomationsServiceError:
            continue


def _env_secret(secret_refs: Mapping[Any, Any], env_name: str) -> str | None:
    target_env = str(secret_refs.get(env_name) or env_name).strip()
    if not target_env:
        return None
    return _optional_string(os.environ.get(target_env))


def _access_policy(raw: Mapping[str, Any]) -> ChannelAccessPolicy:
    payload = raw.get("access_policy")
    policy = dict(payload) if isinstance(payload, Mapping) else {}
    secret_refs = raw.get("secret_refs")
    if (
        str(policy.get("private_policy") or "").strip().lower() == "allowlist"
        and not _string_list(policy.get("allow_from"))
        and isinstance(secret_refs, Mapping)
    ):
        chat_id = _env_secret(secret_refs, "TELEGRAM_CHAT_ID")
        if chat_id:
            policy["allow_from"] = [chat_id]
    try:
        return ChannelAccessPolicy.model_validate(policy)
    except ValueError:
        policy["private_policy"] = "open"
        policy["allow_from"] = []
        if str(policy.get("group_policy") or "").strip().lower() == "allowlist" and not _string_list(
            policy.get("groups")
        ):
            policy["group_policy"] = "disabled"
        return ChannelAccessPolicy.model_validate(policy)


def _endpoint_id(raw: Mapping[str, Any]) -> str:
    value = _optional_string(raw.get("id") or raw.get("endpoint_id"))
    if value:
        return value.lower()
    kind = str(raw.get("kind") or raw.get("transport") or "channel").strip().lower()
    return f"{kind.replace('_', '-')}-default"


def _credential_profile_key(raw: Mapping[str, Any], *, endpoint_id: str) -> str:
    return (_optional_string(raw.get("credential_profile_key")) or endpoint_id).lower()


def _account_id(raw: Mapping[str, Any], *, endpoint_id: str) -> str:
    return (_optional_string(raw.get("account_id")) or endpoint_id).lower()


def _session_policy(raw: Mapping[str, Any]) -> SessionPolicy:
    value = str(raw.get("session_policy") or "main").strip().lower()
    if value in {"main", "per-chat", "per-thread", "per-user-in-group"}:
        return cast(SessionPolicy, value)
    return "main"


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _profile_title(profile_id: str) -> str:
    return profile_id.replace("-", " ").replace("_", " ").title() or "Default"


def _policy_capabilities(runtime_config: ProfileRuntimeConfig) -> tuple[str, ...]:
    enabled_tools = set(runtime_config.enabled_tool_plugins or ())
    capabilities = {"credentials", "apps"}
    if any(tool.startswith("memory_") or tool == "memory" for tool in enabled_tools):
        capabilities.add("memory")
    if "browser_control" in enabled_tools or "browser" in enabled_tools:
        capabilities.add("browser")
    if any(tool in enabled_tools for tool in {"search", "web_search", "web_fetch", "http_request"}):
        capabilities.update({"web", "http"})
    if any(tool.startswith("automation") for tool in enabled_tools):
        capabilities.add("automation")
    if any(tool.startswith("task") for tool in enabled_tools):
        capabilities.add("taskflow")
    if any(tool.startswith("channel") for tool in enabled_tools):
        capabilities.add("apps")
    return tuple(sorted(capabilities))
