"""Runtime helpers for local bootstrap and public setup finalization."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext

from afkbot.cli.presentation.activity_indicator import ActivityIndicator
from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.services.bootstrap_service import seed_missing_global_bootstrap_files
from afkbot.services.install_source import (
    install_source_runtime_payload,
    read_install_source_from_env,
    read_install_source_resolved_target_from_env,
)
from afkbot.services.setup.contracts import SetupConfig
from afkbot.services.setup.runtime_store import (
    read_runtime_config,
    read_runtime_secrets,
    write_runtime_config,
    write_runtime_secrets,
)
from afkbot.services.setup.state import SetupStateSnapshot, write_setup_state
from afkbot.services.llm.provider_catalog import (
    get_provider_spec,
    parse_provider,
    provider_uses_oauth_token,
)
from afkbot.services.llm.token_file_sources import (
    TOKEN_SOURCE_FILE,
    TOKEN_SOURCE_SECRET,
    provider_token_file_field,
    provider_token_source_field,
)
from afkbot.services.profile_runtime import provider_secret_field, run_profile_service_sync
from afkbot.services.profile_runtime.service import reset_profile_services_async
from afkbot.services.update_runtime import resolve_install_source_target
from afkbot.services.wizard.profile_catalog import infer_profile_scenario_id
from afkbot.services.wizard.preview import build_profile_configuration_preview
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
            en="AFKBOT setup is complete.",
            ru="Настройка AFKBOT завершена.",
        )
    )
    echo(
        msg(
            prompt_language,
            en="The default profile is ready and saved for future chats.",
            ru="Профиль по умолчанию готов и сохранён для следующих чатов.",
        )
    )
    echo(
        msg(
            prompt_language,
            en="Next, check local health:",
            ru="Теперь проверьте локальное состояние:",
        )
    )
    echo("  afk doctor")
    echo(
        msg(
            prompt_language,
            en="Then open chat and start working with AFKBOT:",
            ru="Затем откройте чат и начните работать с AFKBOT:",
        )
    )
    echo("  afk chat")
    echo(
        msg(
            prompt_language,
            en="Inside `afk chat`, describe the task in natural language.",
            ru="Внутри `afk chat` просто опишите задачу обычным языком.",
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
    configured = response.get("configured")
    if isinstance(configured, dict):
        scenario = configured.get("wizard_profile_scenario")
        file_access = configured.get("policy_file_access_mode")
        workspace_scope = configured.get("policy_workspace_scope_mode")
        shell_sandbox = configured.get("policy_shell_sandbox_mode")
        capabilities = _text_tuple(configured.get("policy_capabilities"))
        shell_commands = _text_tuple(configured.get("policy_shell_allowed_commands"))
        network_allowlist = _text_tuple(configured.get("policy_network_allowlist"))
        network_mode = _text(configured.get("policy_network_mode"), default="custom")
        profile_payload = response.get("default_profile")
        allowed_directories: tuple[str, ...] = ()
        if isinstance(profile_payload, dict):
            policy_payload = profile_payload.get("policy")
            if isinstance(policy_payload, dict):
                allowed_directories = _text_tuple(policy_payload.get("allowed_directories"))
        credential_status = (
            (
                "llm_api_key_configured"
                if configured.get("llm_api_key_configured")
                else "llm_api_key_missing"
            ),
            (
                "credentials_master_keys_configured"
                if configured.get("credentials_master_keys_configured")
                else "credentials_master_keys_missing"
            ),
        )
        if (
            isinstance(scenario, str)
            and isinstance(file_access, str)
            and isinstance(workspace_scope, str)
            and isinstance(shell_sandbox, str)
        ):
            for line in build_profile_configuration_preview(
                scenario_id=scenario,
                capabilities=capabilities,
                file_access_mode=file_access,
                workspace_scope_mode=workspace_scope,
                allowed_directories=allowed_directories,
                shell_sandbox_mode=shell_sandbox,
                shell_allowed_commands=shell_commands,
                network_mode=network_mode,
                network_allowlist=network_allowlist,
                credential_status=credential_status,
                lang=prompt_language,
            ).lines:
                echo(line)
        if isinstance(scenario, str) and scenario.strip():
            echo(
                msg(
                    prompt_language,
                    en=f"Profile wizard scenario: {scenario}",
                    ru=f"Сценарий мастера профиля: {scenario}",
                )
            )
        if isinstance(file_access, str) and isinstance(workspace_scope, str):
            echo(
                msg(
                    prompt_language,
                    en=f"File boundary: {file_access}, scope={workspace_scope}",
                    ru=f"Граница файлов: {file_access}, область={workspace_scope}",
                )
            )
        if isinstance(shell_sandbox, str):
            echo(f"Shell sandbox: {shell_sandbox}")

    public_runtime_url = response.get("public_runtime_url")
    public_chat_api_url = response.get("public_chat_api_url")
    nginx_config_path = response.get("nginx_config_path")

    if isinstance(public_runtime_url, str) and public_runtime_url.strip():
        echo(
            msg(
                prompt_language,
                en=f"Public runtime URL: {public_runtime_url}",
                ru=f"Публичный URL службы AFKBOT: {public_runtime_url}",
            )
        )
    if isinstance(public_chat_api_url, str) and public_chat_api_url.strip():
        echo(
            msg(
                prompt_language,
                en=f"Public chat/api URL: {public_chat_api_url}",
                ru=f"Публичный URL чата/API: {public_chat_api_url}",
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
                ru=f"Конфигурация nginx: {nginx_config_path}",
            )
        )


def _text(value: object, *, default: str = "") -> str:
    if isinstance(value, str):
        return value
    return default


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


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
                policy_shell_sandbox_mode=config.policy_shell_sandbox_mode,
                policy_shell_allowed_commands=config.policy_shell_allowed_commands,
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
                update_notices_enabled=config.update_notices_enabled,
                policy_setup_mode=config.policy_setup_mode,
                policy_enabled=profile.policy.enabled,
                policy_preset=profile.policy.preset,
                policy_confirmation_mode=config.policy_confirmation_mode,
                policy_capabilities=profile.policy.capabilities,
                policy_allowed_tools=(),
                policy_file_access_mode=config.policy_file_access_mode,
                policy_allowed_directories=profile.policy.allowed_directories,
                policy_shell_sandbox_mode=profile.policy.shell_sandbox_mode,
                policy_shell_allowed_commands=profile.policy.shell_allowed_commands,
                policy_network_mode=config.policy_network_mode,
                policy_network_allowlist=profile.policy.network_allowlist,
                policy_workspace_scope_mode=config.policy_workspace_scope_mode,
                wizard_profile_scenario=infer_profile_scenario_id(
                    capabilities=profile.policy.capabilities,
                    file_access_mode=config.policy_file_access_mode,
                    workspace_scope_mode=config.policy_workspace_scope_mode,
                    shell_sandbox_mode=profile.policy.shell_sandbox_mode,
                    shell_allowed_commands=profile.policy.shell_allowed_commands,
                    network_mode=config.policy_network_mode,
                    network_allowlist=profile.policy.network_allowlist,
                ),
                wizard_setup_depth=config.wizard_setup_depth,
                wizard_work_contexts=config.wizard_work_contexts,
                wizard_actions=config.wizard_actions,
                wizard_isolation=config.wizard_isolation,
                wizard_confirmation=config.wizard_confirmation,
                wizard_network=config.wizard_network,
                wizard_network_allowlist=config.wizard_network_allowlist,
            ),
        )

    response = _build_setup_response(
        config=config,
        runtime_config=runtime_config_payload,
        profile=profile.model_dump(mode="json"),
    )
    return response


def _build_platform_runtime_config_payload(*, config: SetupConfig) -> dict[str, object]:
    install_source = read_install_source_from_env()
    resolved_install_target = read_install_source_resolved_target_from_env()
    if install_source is not None and resolved_install_target is None:
        resolved_install_target = resolve_install_source_target(install_source)
    payload = {
        "db_url": config.db_url,
        "deployment_mode": "self_hosted",
        "runtime_public_bind_policy": "local_only",
        "plugin_api_auth_required": True,
        "partyflow_webhook_signing_required": False,
        "managed_database_isolation_mode": "database_per_bot",
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
        "update_notices_enabled": config.update_notices_enabled,
    }
    payload.update(
        install_source_runtime_payload(
            install_source,
            resolved_target=resolved_install_target,
        )
    )
    return payload


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
            "policy_shell_sandbox_mode": config.policy_shell_sandbox_mode,
            "policy_shell_allowed_commands": list(config.policy_shell_allowed_commands),
            "policy_network_mode": config.policy_network_mode,
            "policy_network_allowlist": list(config.policy_network_allowlist),
            "wizard_setup_depth": config.wizard_setup_depth,
            "wizard_work_contexts": list(config.wizard_work_contexts),
            "wizard_actions": list(config.wizard_actions),
            "wizard_isolation": config.wizard_isolation,
            "wizard_confirmation": config.wizard_confirmation,
            "wizard_network": config.wizard_network,
            "wizard_network_allowlist": list(config.wizard_network_allowlist),
            "update_notices_enabled": config.update_notices_enabled,
        }
    )
    if not config.update_notices_enabled:
        payload["update_notice_skip_target"] = None
        payload["update_notice_remind_target"] = None
        payload["update_notice_remind_until"] = None
    if provider_base_url_field:
        payload[provider_base_url_field] = config.llm_base_url or None
    return payload


def _build_runtime_secrets_payload(
    *,
    config: SetupConfig,
    existing_runtime_secrets: dict[str, str],
) -> dict[str, str]:
    payload = dict(existing_runtime_secrets)
    payload.update(
        {
            key: value.strip()
            for key, value in config.runtime_secrets_update.items()
            if isinstance(value, str) and value.strip()
        }
    )
    provider_id = parse_provider(config.llm_provider)
    uses_oauth = provider_uses_oauth_token(provider_id)
    uses_file_backed_provider_token = _uses_file_backed_provider_token(
        llm_provider=config.llm_provider,
        runtime_secrets=payload,
    )
    _apply_provider_token_source_cleanup(llm_provider=config.llm_provider, runtime_secrets=payload)
    if config.credentials_master_keys:
        payload["credentials_master_keys"] = config.credentials_master_keys
    if config.llm_api_key and not uses_file_backed_provider_token:
        if not uses_oauth:
            payload["llm_api_key"] = config.llm_api_key
        payload[provider_secret_field(config.llm_provider)] = config.llm_api_key
    return payload


def _build_default_profile_runtime_secrets(*, config: SetupConfig) -> dict[str, str] | None:
    payload = {
        key: value.strip()
        for key, value in config.runtime_secrets_update.items()
        if isinstance(value, str) and value.strip()
    }
    provider_id = parse_provider(config.llm_provider)
    uses_file_backed_provider_token = _uses_file_backed_provider_token(
        llm_provider=config.llm_provider,
        runtime_secrets=payload,
    )
    _apply_provider_token_source_cleanup(llm_provider=config.llm_provider, runtime_secrets=payload)
    if config.llm_api_key and not uses_file_backed_provider_token:
        payload[provider_secret_field(config.llm_provider)] = config.llm_api_key
        if not provider_uses_oauth_token(provider_id):
            payload["llm_api_key"] = config.llm_api_key
    if not payload:
        return None
    return payload


def _uses_file_backed_provider_token(*, llm_provider: str, runtime_secrets: dict[str, str]) -> bool:
    source_field = provider_token_source_field(llm_provider)
    return source_field is not None and runtime_secrets.get(source_field) == TOKEN_SOURCE_FILE


def _apply_provider_token_source_cleanup(*, llm_provider: str, runtime_secrets: dict[str, str]) -> None:
    source_field = provider_token_source_field(llm_provider)
    if source_field is None:
        return
    source = runtime_secrets.get(source_field)
    if source == TOKEN_SOURCE_FILE:
        runtime_secrets.pop(provider_secret_field(llm_provider), None)
        runtime_secrets.pop("llm_api_key", None)
    elif source == TOKEN_SOURCE_SECRET:
        file_field = provider_token_file_field(llm_provider)
        if file_field is not None:
            runtime_secrets.pop(file_field, None)


def _build_setup_response(
    *,
    config: SetupConfig,
    runtime_config: dict[str, object],
    profile: dict[str, object],
) -> dict[str, object]:
    wizard_profile_scenario = infer_profile_scenario_id(
        capabilities=config.policy_capabilities,
        file_access_mode=config.policy_file_access_mode,
        workspace_scope_mode=config.policy_workspace_scope_mode,
        shell_sandbox_mode=config.policy_shell_sandbox_mode,
        shell_allowed_commands=config.policy_shell_allowed_commands,
        network_mode=config.policy_network_mode,
        network_allowlist=config.policy_network_allowlist,
    )
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
            "policy_workspace_scope_mode": config.policy_workspace_scope_mode,
            "policy_shell_sandbox_mode": config.policy_shell_sandbox_mode,
            "policy_shell_allowed_commands": list(config.policy_shell_allowed_commands),
            "policy_network_mode": config.policy_network_mode,
            "policy_network_allowlist": list(config.policy_network_allowlist),
            "wizard_profile_scenario": wizard_profile_scenario,
            "wizard_setup_depth": config.wizard_setup_depth,
            "wizard_work_contexts": list(config.wizard_work_contexts),
            "wizard_actions": list(config.wizard_actions),
            "wizard_isolation": config.wizard_isolation,
            "wizard_confirmation": config.wizard_confirmation,
            "wizard_network": config.wizard_network,
            "wizard_network_allowlist": list(config.wizard_network_allowlist),
        },
        "default_profile": profile,
    }
