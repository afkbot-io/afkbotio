"""Runtime helpers for local bootstrap and public setup finalization."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext

from afkbot.cli.presentation.activity_indicator import ActivityIndicator
from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.services.bootstrap_service import seed_missing_global_bootstrap_files
from afkbot.services.setup.contracts import SetupConfig
from afkbot.services.setup.runtime_store import (
    read_runtime_config,
    read_runtime_secrets,
    write_runtime_config,
    write_runtime_secrets,
)
from afkbot.services.setup.state import SetupStateSnapshot, write_setup_state
from afkbot.services.llm.provider_catalog import get_provider_spec, parse_provider
from afkbot.services.profile_runtime import provider_secret_field, run_profile_service_sync
from afkbot.services.profile_runtime.service import reset_profile_services_async
from afkbot.settings import Settings, get_settings


def execute_setup_runtime(
    *,
    settings: Settings,
    config: SetupConfig,
    interactive: bool,
    bootstrap_only: bool,
    prompt_language: PromptLanguage,
    activity_indicator_cls: type[ActivityIndicator] = ActivityIndicator,
) -> dict[str, object]:
    """Seed platform runtime state or finalize setup by creating the default profile."""

    if bootstrap_only:
        response = _seed_platform_runtime(settings=settings, config=config)
        get_settings.cache_clear()
        return response

    response = _finalize_setup_runtime(
        settings=settings,
        config=config,
        interactive=interactive,
        prompt_language=prompt_language,
        activity_indicator_cls=activity_indicator_cls,
    )
    get_settings.cache_clear()
    return response


def render_setup_success(
    *,
    interactive: bool,
    prompt_language: PromptLanguage,
    response: dict[str, object] | None = None,
) -> None:
    """Print post-setup success summary for interactive CLI usage."""

    if not interactive:
        return
    from typer import echo

    echo(
        msg(
            prompt_language,
            en="Default AFKBOT profile configured successfully.",
            ru="Профиль AFKBOT по умолчанию успешно настроен.",
        )
    )
    echo(
        msg(
            prompt_language,
            en="Platform prerequisites were already available; setup configured the default profile.",
            ru="Базовые требования платформы уже были готовы; setup настроил профиль по умолчанию.",
        )
    )
    echo(
        msg(
            prompt_language,
            en="Next steps: run `afk doctor` and then `afk chat`.",
            ru="Дальше: запустите `afk doctor`, затем `afk chat`.",
        )
    )
    echo(
        msg(
            prompt_language,
            en="To change the default profile later, run `afk profile update default`.",
            ru="Чтобы позже изменить профиль по умолчанию, выполните `afk profile update default`.",
        )
    )
    if not isinstance(response, dict):
        return

    public_runtime_url = response.get("public_runtime_url")
    public_chat_api_url = response.get("public_chat_api_url")
    nginx_config_path = response.get("nginx_config_path")

    if isinstance(public_runtime_url, str) and public_runtime_url.strip():
        echo(
            msg(
                prompt_language,
                en=f"Public runtime URL: {public_runtime_url}",
                ru=f"Публичный runtime URL: {public_runtime_url}",
            )
        )
    if isinstance(public_chat_api_url, str) and public_chat_api_url.strip():
        echo(
            msg(
                prompt_language,
                en=f"Public chat/api URL: {public_chat_api_url}",
                ru=f"Публичный chat/api URL: {public_chat_api_url}",
            )
        )
        echo(
            msg(
                prompt_language,
                en="Connect example: `afk connect --session desktop-session`",
                ru="Пример подключения: `afk connect --session desktop-session`",
            )
        )
    if isinstance(nginx_config_path, str) and nginx_config_path.strip():
        echo(
            msg(
                prompt_language,
                en=f"Nginx config: {nginx_config_path}",
                ru=f"Конфиг nginx: {nginx_config_path}",
            )
        )


def _seed_platform_runtime(*, settings: Settings, config: SetupConfig) -> dict[str, object]:
    seed_missing_global_bootstrap_files(settings)
    current_config = dict(read_runtime_config(settings))
    current_config.update(_build_platform_runtime_config_payload(config=config))
    write_runtime_config(settings, config=current_config)

    return {
        "ok": True,
        "error_code": None,
        "reason": None,
        "database": "sqlite",
        "db_url": config.db_url,
        "project_root": str(settings.root_dir),
        "runtime_bind": config.runtime_host,
        "runtime_port": config.runtime_port,
        "api_port": config.runtime_port + 1,
        "public_runtime_url": config.public_runtime_url or None,
        "public_chat_api_url": config.public_chat_api_url or None,
    }


def _finalize_setup_runtime(
    *,
    settings: Settings,
    config: SetupConfig,
    interactive: bool,
    prompt_language: PromptLanguage,
    activity_indicator_cls: type[ActivityIndicator],
) -> dict[str, object]:
    seed_missing_global_bootstrap_files(settings)
    activity = (
        activity_indicator_cls(
            label=msg(
                prompt_language,
                en="Configuring default AFKBOT profile",
                ru="Настраиваю профиль AFKBOT по умолчанию",
            )
        )
        if interactive
        else nullcontext()
    )
    with activity:
        current_runtime_config = dict(read_runtime_config(settings))
        current_runtime_secrets = dict(read_runtime_secrets(settings))
        runtime_config_payload = _build_runtime_config_payload(
            config=config,
            existing_runtime_config=current_runtime_config,
        )
        runtime_secrets_payload = _build_runtime_secrets_payload(
            config=config,
            existing_runtime_secrets=current_runtime_secrets,
        )
        write_runtime_config(settings, config=runtime_config_payload)
        write_runtime_secrets(settings, secrets=runtime_secrets_payload)
        get_settings.cache_clear()
        asyncio.run(reset_profile_services_async())
        effective_settings = get_settings()
        profile = run_profile_service_sync(
            effective_settings,
            lambda service: service.bootstrap_default(
                runtime_config=config.default_profile_runtime_config,
                runtime_secrets=_build_default_profile_runtime_secrets(config=config),
                policy_enabled=config.policy_enabled,
                policy_preset=config.policy_preset,
                policy_capabilities=config.policy_capabilities,
                policy_file_access_mode=config.policy_file_access_mode,
                policy_allowed_directories=config.policy_allowed_directories,
                policy_network_allowlist=config.policy_network_allowlist,
            ),
        )
        write_setup_state(
            effective_settings,
            SetupStateSnapshot(
                env_file=str(config.env_file),
                db_url=config.db_url,
                llm_provider=config.llm_provider,
                llm_model=config.chat_model,
                llm_thinking_level=config.llm_thinking_level,
                llm_proxy_type=config.llm_proxy_type,
                llm_proxy_configured=bool(config.llm_proxy_url),
                credentials_master_keys_configured=bool(config.credentials_master_keys),
                runtime_host=config.runtime_host,
                runtime_port=config.runtime_port,
                nginx_enabled=config.nginx_enabled,
                nginx_port=config.nginx_port,
                public_runtime_url=config.public_runtime_url,
                public_chat_api_url=config.public_chat_api_url,
                prompt_language=config.prompt_language,
                policy_setup_mode=config.policy_setup_mode,
                policy_enabled=profile.policy.enabled,
                policy_preset=profile.policy.preset,
                policy_confirmation_mode=config.policy_confirmation_mode,
                policy_capabilities=profile.policy.capabilities,
                policy_allowed_tools=(),
                policy_file_access_mode=config.policy_file_access_mode,
                policy_allowed_directories=profile.policy.allowed_directories,
                policy_network_mode=config.policy_network_mode,
                policy_network_allowlist=profile.policy.network_allowlist,
            ),
        )

    response = _build_setup_response(
        config=config,
        runtime_config=runtime_config_payload,
        profile=profile.model_dump(mode="json"),
    )
    return response


def _build_platform_runtime_config_payload(*, config: SetupConfig) -> dict[str, object]:
    return {
        "db_url": config.db_url,
        "runtime_host": config.runtime_host,
        "runtime_port": config.runtime_port,
        "nginx_enabled": config.nginx_enabled,
        "nginx_port": config.nginx_port,
        "nginx_runtime_host": config.nginx_runtime_host or None,
        "nginx_runtime_public_port": config.nginx_runtime_public_port,
        "nginx_runtime_https": config.nginx_runtime_https,
        "nginx_api_host": config.nginx_api_host or None,
        "nginx_api_public_port": config.nginx_api_public_port,
        "nginx_api_https": config.nginx_api_https,
        "certbot_email": config.certbot_email or None,
        "public_runtime_url": config.public_runtime_url or None,
        "public_chat_api_url": config.public_chat_api_url or None,
        "auto_install_deps": config.auto_install_deps,
        "prompt_language": config.prompt_language,
    }


def _build_runtime_config_payload(
    *,
    config: SetupConfig,
    existing_runtime_config: dict[str, object],
) -> dict[str, object]:
    payload = dict(existing_runtime_config)
    payload.update(_build_platform_runtime_config_payload(config=config))
    provider_id = parse_provider(config.llm_provider)
    provider_spec = get_provider_spec(provider_id)
    provider_base_url_field = provider_spec.base_url_env_name.replace("AFKBOT_", "").lower()
    payload.update(
        {
            "llm_provider": config.llm_provider,
            "llm_model": config.chat_model,
            "llm_thinking_level": config.llm_thinking_level,
            "llm_base_url": config.llm_base_url or None,
            "llm_proxy_type": config.llm_proxy_type,
            "llm_proxy_url": config.llm_proxy_url or None,
            "custom_interface": config.custom_interface,
            "policy_setup_mode": config.policy_setup_mode,
            "policy_enabled": config.policy_enabled,
            "policy_preset": config.policy_preset,
            "policy_confirmation_mode": config.policy_confirmation_mode,
            "policy_capabilities": list(config.policy_capabilities),
            "policy_file_access_mode": config.policy_file_access_mode,
            "policy_workspace_scope": config.policy_workspace_scope_mode,
            "policy_allowed_directories": list(config.policy_allowed_directories),
            "policy_network_mode": config.policy_network_mode,
            "policy_network_allowlist": list(config.policy_network_allowlist),
        }
    )
    if provider_base_url_field:
        payload[provider_base_url_field] = config.llm_base_url or None
    return payload


def _build_runtime_secrets_payload(
    *,
    config: SetupConfig,
    existing_runtime_secrets: dict[str, str],
) -> dict[str, str]:
    payload = dict(existing_runtime_secrets)
    if config.credentials_master_keys:
        payload["credentials_master_keys"] = config.credentials_master_keys
    if config.llm_api_key:
        payload["llm_api_key"] = config.llm_api_key
        payload[provider_secret_field(config.llm_provider)] = config.llm_api_key
    return payload


def _build_default_profile_runtime_secrets(*, config: SetupConfig) -> dict[str, str] | None:
    if not config.llm_api_key:
        return None
    return {
        "llm_api_key": config.llm_api_key,
        provider_secret_field(config.llm_provider): config.llm_api_key,
    }


def _build_setup_response(
    *,
    config: SetupConfig,
    runtime_config: dict[str, object],
    profile: dict[str, object],
) -> dict[str, object]:
    return {
        "ok": True,
        "error_code": None,
        "reason": None,
        "database": "sqlite",
        "db_url": config.db_url,
        "public_runtime_url": runtime_config.get("public_runtime_url"),
        "public_chat_api_url": runtime_config.get("public_chat_api_url"),
        "nginx_config_path": runtime_config.get("nginx_config_path"),
        "configured": {
            "llm_provider": config.llm_provider,
            "chat_model": config.chat_model,
            "thinking_level": config.llm_thinking_level,
            "llm_api_key_configured": bool(config.llm_api_key),
            "credentials_master_keys_configured": bool(config.credentials_master_keys),
            "policy_enabled": config.policy_enabled,
            "policy_setup_mode": config.policy_setup_mode,
            "policy_preset": config.policy_preset,
            "policy_capabilities": list(config.policy_capabilities),
            "policy_file_access_mode": config.policy_file_access_mode,
            "policy_network_mode": config.policy_network_mode,
            "policy_network_allowlist": list(config.policy_network_allowlist),
        },
        "default_profile": profile,
    }
