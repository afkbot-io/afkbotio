"""High-level setup config assembly from prompt/input resolvers."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.presentation.setup_prompts import (
    PromptLanguage,
    msg,
    prompt_secret_ack,
)
from afkbot.services.setup.contracts import (
    PolicyFileAccessMode,
    PolicyNetworkMode,
    PolicySetupMode,
    SetupConfig,
    WILDCARD_NETWORK_HOST,
)
from afkbot.services.setup.defaults import (
    read_bool_default,
    read_int_default,
    recommended_policy_capabilities,
)
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.services.setup.profile_resolution import (
    ResolvedProfileRuntimeCore,
    ResolvedProfilePolicyInputs,
    build_profile_runtime_config,
    resolve_profile_policy_inputs,
    resolve_profile_runtime_core,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.setup.policy_inputs import (
    confirmation_mode_for_preset,
    has_explicit_policy_overrides,
    resolve_policy_setup_mode,
)
from afkbot.services.setup.provider_inputs import (
    resolve_api_key,
    resolve_credentials_master_keys,
    resolve_nginx_enabled,
    resolve_nginx_plan,
    resolve_port,
    resolve_text,
)
from afkbot.services.llm.token_verifier import verify_provider_token
from afkbot.services.policy import PolicyPresetLevel, resolve_allowed_directories_for_scope_mode
from afkbot.services.runtime_ports import (
    resolve_default_runtime_port,
)
from afkbot.settings import Settings


def _resolve_runtime_port_default_from_inputs(
    *,
    settings: Settings,
    defaults: dict[str, str],
    runtime_config: dict[str, object],
    host: str,
) -> int:
    """Resolve the runtime-port default while preserving operator-configured values."""

    del defaults
    return resolve_default_runtime_port(
        settings=settings,
        host=host,
        runtime_config=runtime_config,
    )


def _infer_policy_setup_mode_from_resolved_policy(
    resolved_policy: ResolvedProfilePolicyInputs,
) -> str:
    """Infer the high-level setup mode label from one already-resolved policy."""

    is_recommended = (
        resolved_policy.enabled is True
        and resolved_policy.preset == PolicyPresetLevel.MEDIUM.value
        and resolved_policy.capabilities == recommended_policy_capabilities()
        and resolved_policy.network_mode == PolicyNetworkMode.UNRESTRICTED.value
        and resolved_policy.network_allowlist == (WILDCARD_NETWORK_HOST,)
        and resolved_policy.file_access_mode == PolicyFileAccessMode.READ_WRITE.value
        and resolved_policy.workspace_scope_mode == "profile_only"
    )
    return (
        PolicySetupMode.RECOMMENDED.value
        if is_recommended
        else PolicySetupMode.CUSTOM.value
    )


def build_default_profile_runtime_config(
    *,
    runtime_core: ResolvedProfileRuntimeCore,
    base_runtime: ProfileRuntimeConfig | None = None,
) -> ProfileRuntimeConfig:
    """Build the persisted default-profile runtime config for one setup run.

    Setup only exposes provider/model/base URL/proxy/thinking directly, so the
    rest of the profile runtime state must be preserved from the existing
    profile when one already exists.
    """

    return build_profile_runtime_config(
        base_runtime=base_runtime,
        runtime_core=runtime_core,
        llm_history_turns=None,
        tool_plugins=(),
        memory_auto_search_enabled=None,
        memory_auto_search_scope_mode=None,
        memory_auto_search_limit=None,
        memory_auto_search_include_global=None,
        memory_auto_search_chat_limit=None,
        memory_auto_search_global_limit=None,
        memory_global_fallback_enabled=None,
        memory_auto_context_item_chars=None,
        memory_auto_save_enabled=None,
        memory_auto_save_scope_mode=None,
        memory_auto_promote_enabled=None,
        memory_auto_save_kinds=(),
        memory_auto_save_max_chars=None,
        session_compaction_enabled=None,
        session_compaction_trigger_turns=None,
        session_compaction_keep_recent_turns=None,
        session_compaction_max_chars=None,
        session_compaction_prune_raw_turns=None,
    )


def ensure_security_ack(
    *,
    interactive: bool,
    accept_risk: bool,
    lang: PromptLanguage,
) -> None:
    """Require explicit acknowledgment before setup continues."""

    if accept_risk:
        return
    if not interactive:
        raise_usage_error(
            msg(
                lang,
                en="Use --accept-risk in --yes mode.",
                ru="Используйте --accept-risk вместе с --yes.",
            )
        )
    confirmed = prompt_secret_ack(lang=lang)
    if not confirmed:
        raise typer.Exit(code=1)


def collect_setup_config(
    *,
    settings: Settings,
    defaults: dict[str, str],
    env_file: Path,
    interactive: bool,
    lang: PromptLanguage,
    llm_provider: str | None,
    chat_model: str | None,
    thinking_level: str | None,
    llm_api_key_file: Path | None,
    llm_base_url: str | None,
    custom_interface: str | None,
    skip_llm_token_verify: bool,
    llm_proxy_type: str | None,
    llm_proxy_url: str | None,
    runtime_host: str | None,
    runtime_port: int | None,
    nginx_enabled: bool | None,
    nginx_port: int | None,
    nginx_runtime_host: str | None,
    nginx_runtime_https: bool | None,
    nginx_api_host: str | None,
    nginx_api_https: bool | None,
    certbot_email: str | None,
    policy_enabled: bool | None,
    policy_preset: str | None,
    policy_capability: tuple[str, ...],
    policy_file_access_mode: str | None,
    policy_workspace_scope: str | None,
    policy_network_host: tuple[str, ...],
    auto_install_deps: bool | None,
    default_profile_base_runtime_config: ProfileRuntimeConfig | None = None,
    resolved_runtime_core: ResolvedProfileRuntimeCore | None = None,
    resolved_api_key: str | None = None,
    resolved_runtime_secrets_update: dict[str, str] | None = None,
    resolved_policy_inputs: ResolvedProfilePolicyInputs | None = None,
    default_profile_runtime_config_override: ProfileRuntimeConfig | None = None,
    profile_setup_only: bool = False,
    platform_seed_only: bool = False,
) -> SetupConfig:
    """Collect and normalize all setup answers into one config object."""

    provider_default = defaults.get("AFKBOT_LLM_PROVIDER", settings.llm_provider)
    runtime_core = resolved_runtime_core or resolve_profile_runtime_core(
        interactive=interactive and not platform_seed_only,
        lang=lang,
        settings=settings,
        provider_value=llm_provider,
        model_value=chat_model,
        thinking_level_value=thinking_level,
        thinking_level_prompt=msg(
            lang,
            en="Default thinking level (low, medium, high, very_high)",
            ru="Уровень раздумия по умолчанию (low, medium, high, very_high)",
        ),
        base_url_value=llm_base_url,
        base_url_prompt=msg(
            lang,
            en="Provider base URL",
            ru="Base URL провайдера",
        ),
        custom_interface_value=custom_interface,
        proxy_type_value=llm_proxy_type,
        proxy_url_value=llm_proxy_url,
        planning_mode_value=None,
        default_provider=provider_default,
        default_model=defaults.get("AFKBOT_LLM_MODEL", settings.llm_model),
        default_thinking_level=defaults.get("AFKBOT_LLM_THINKING_LEVEL", settings.llm_thinking_level),
        default_base_url=defaults.get("AFKBOT_LLM_BASE_URL", settings.llm_base_url or ""),
        default_custom_interface=str(defaults.get("AFKBOT_CUSTOM_INTERFACE", settings.custom_interface)),
        default_proxy_type=defaults.get("AFKBOT_LLM_PROXY_TYPE", settings.llm_proxy_type),
        default_proxy_url=defaults.get("AFKBOT_LLM_PROXY_URL", settings.llm_proxy_url or ""),
        default_planning_mode=None,
    )
    provider_id = runtime_core.provider_id
    provider = runtime_core.llm_provider
    model = runtime_core.llm_model
    thinking_level_resolved = runtime_core.llm_thinking_level or "medium"
    custom_interface_resolved = runtime_core.custom_interface
    base_url = runtime_core.llm_base_url
    proxy_type = runtime_core.llm_proxy_type
    proxy_url = runtime_core.llm_proxy_url
    if platform_seed_only:
        api_key = ""
    else:
        api_key = (resolved_api_key or "").strip() or resolve_api_key(
            provider_id=provider_id,
            interactive=interactive,
            defaults=defaults,
            key_file=llm_api_key_file,
            lang=lang,
        )
    if not platform_seed_only and not skip_llm_token_verify:
        verification = verify_provider_token(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            proxy_url=proxy_url if proxy_type != "none" else None,
        )
        if not verification.ok:
            raise typer.BadParameter(
                verification.reason
                or msg(
                    lang,
                    en="Provider credential verification failed.",
                    ru="Не удалось проверить credential провайдера.",
                )
            )

    if platform_seed_only:
        policy_setup_mode_resolved = PolicySetupMode.RECOMMENDED.value
        policy_preset_resolved = PolicyPresetLevel.MEDIUM.value
        policy_enabled_resolved = True
        policy_capabilities_resolved = recommended_policy_capabilities()
        policy_network_mode_resolved = PolicyNetworkMode.UNRESTRICTED.value
        policy_network_allowlist_resolved = cast(tuple[str, ...], (WILDCARD_NETWORK_HOST,))
        policy_file_access_mode_resolved = PolicyFileAccessMode.READ_WRITE.value
        policy_workspace_scope_mode_resolved = "profile_only"
    else:
        explicit_policy_overrides = has_explicit_policy_overrides(
            policy_enabled=policy_enabled,
            policy_preset=policy_preset,
            policy_capability=policy_capability,
            policy_file_access_mode=policy_file_access_mode,
            policy_workspace_scope=policy_workspace_scope,
            policy_network_host=policy_network_host,
        )
        if resolved_policy_inputs is not None:
            policy_setup_mode_resolved = _infer_policy_setup_mode_from_resolved_policy(
                resolved_policy_inputs
            )
            policy_preset_resolved = resolved_policy_inputs.preset
            policy_enabled_resolved = resolved_policy_inputs.enabled
            policy_capabilities_resolved = resolved_policy_inputs.capabilities
            policy_network_mode_resolved = resolved_policy_inputs.network_mode
            policy_network_allowlist_resolved = resolved_policy_inputs.network_allowlist
            policy_file_access_mode_resolved = resolved_policy_inputs.file_access_mode
            policy_workspace_scope_mode_resolved = resolved_policy_inputs.workspace_scope_mode
        else:
            policy_setup_mode_resolved = resolve_policy_setup_mode(
                interactive=interactive,
                defaults=defaults,
                explicit_policy_overrides=explicit_policy_overrides,
                lang=lang,
            )
            if (
                policy_setup_mode_resolved == PolicySetupMode.RECOMMENDED.value
                and not explicit_policy_overrides
            ):
                policy_preset_resolved = PolicyPresetLevel.MEDIUM.value
                policy_enabled_resolved = True
                policy_capabilities_resolved = recommended_policy_capabilities()
                policy_network_mode_resolved = PolicyNetworkMode.UNRESTRICTED.value
                policy_network_allowlist_resolved = cast(tuple[str, ...], (WILDCARD_NETWORK_HOST,))
                policy_file_access_mode_resolved = PolicyFileAccessMode.READ_WRITE.value
                policy_workspace_scope_mode_resolved = "profile_only"
            else:
                resolved_policy = resolve_profile_policy_inputs(
                    interactive=interactive,
                    lang=lang,
                    root_dir=settings.root_dir,
                    profile_root=settings.profiles_dir / "default",
                    defaults=defaults,
                    policy_enabled_value=policy_enabled,
                    policy_preset_value=policy_preset,
                    policy_capability_values=policy_capability,
                    policy_file_access_mode_value=policy_file_access_mode,
                    policy_workspace_scope_value=policy_workspace_scope,
                    policy_allowed_dir_values=(),
                    policy_network_host_values=policy_network_host,
                    allow_custom_workspace_scope=False,
                )
                policy_preset_resolved = resolved_policy.preset
                policy_enabled_resolved = resolved_policy.enabled
                policy_capabilities_resolved = resolved_policy.capabilities
                policy_network_mode_resolved = resolved_policy.network_mode
                policy_network_allowlist_resolved = resolved_policy.network_allowlist
                policy_file_access_mode_resolved = resolved_policy.file_access_mode
                policy_workspace_scope_mode_resolved = resolved_policy.workspace_scope_mode
    policy_confirmation_mode_resolved = confirmation_mode_for_preset(policy_preset_resolved)
    if (
        policy_setup_mode_resolved == PolicySetupMode.RECOMMENDED.value
        and not platform_seed_only
        and not explicit_policy_overrides
    ):
        policy_allowed_directories_resolved = resolve_allowed_directories_for_scope_mode(
            root_dir=settings.root_dir,
            profile_root=settings.profiles_dir / "default",
            scope_mode=policy_workspace_scope_mode_resolved,
        )
    elif platform_seed_only:
        policy_allowed_directories_resolved = resolve_allowed_directories_for_scope_mode(
            root_dir=settings.root_dir,
            profile_root=settings.profiles_dir / "default",
            scope_mode=policy_workspace_scope_mode_resolved,
        )
    else:
        policy_allowed_directories_resolved = (
            resolved_policy_inputs.allowed_directories
            if resolved_policy_inputs is not None
            else resolved_policy.allowed_directories
        )

    credentials_master_keys = (
        ""
        if platform_seed_only
        else resolve_credentials_master_keys(
            interactive=interactive,
            existing=defaults.get("AFKBOT_CREDENTIALS_MASTER_KEYS", ""),
            lang=lang,
        )
    )
    db_url_resolved = Settings(
        root_dir=settings.root_dir,
        db_url=str(defaults.get("AFKBOT_DB_URL", settings.db_url)).strip() or settings.db_url,
    ).db_url
    persisted_runtime_config = read_runtime_config(settings)

    if profile_setup_only or platform_seed_only:
        runtime_host_resolved = (runtime_host or defaults.get("AFKBOT_RUNTIME_HOST", settings.runtime_host)).strip()
        runtime_port_default = _resolve_runtime_port_default_from_inputs(
            settings=settings,
            defaults=defaults,
            runtime_config=persisted_runtime_config,
            host=runtime_host_resolved,
        )
        runtime_port_resolved = int(
            runtime_port
            if runtime_port is not None
            else runtime_port_default
        )
        nginx_enabled_resolved = (
            nginx_enabled
            if nginx_enabled is not None
            else read_bool_default(defaults.get("AFKBOT_NGINX_ENABLED"), settings.nginx_enabled)
        )
        nginx_port_resolved = int(
            nginx_port
            if nginx_port is not None
            else read_int_default(defaults.get("AFKBOT_NGINX_PORT"), settings.nginx_port)
        )
        nginx_plan = resolve_nginx_plan(
            nginx_enabled=nginx_enabled_resolved,
            runtime_port=runtime_port_resolved,
            api_port=runtime_port_resolved + 1,
            runtime_host_value=nginx_runtime_host,
            api_host_value=nginx_api_host,
            runtime_https_value=nginx_runtime_https,
            api_https_value=nginx_api_https,
            certbot_email_value=certbot_email,
            runtime_host_default=defaults.get("AFKBOT_NGINX_RUNTIME_HOST", ""),
            api_host_default=defaults.get("AFKBOT_NGINX_API_HOST", ""),
            runtime_https_default=read_bool_default(defaults.get("AFKBOT_NGINX_RUNTIME_HTTPS"), False),
            api_https_default=read_bool_default(defaults.get("AFKBOT_NGINX_API_HTTPS"), False),
            certbot_email_default=defaults.get("AFKBOT_CERTBOT_EMAIL", ""),
            interactive=False,
            lang=lang,
        )
        auto_install_deps_resolved = (
            auto_install_deps
            if auto_install_deps is not None
            else read_bool_default(defaults.get("AFKBOT_AUTO_INSTALL_DEPS"), True)
        )
    else:
        runtime_host_resolved = resolve_text(
            value=runtime_host,
            interactive=interactive,
            prompt=msg(lang, en="Runtime host", ru="Хост runtime"),
            default=defaults.get("AFKBOT_RUNTIME_HOST", settings.runtime_host),
            lang=lang,
        )
        runtime_port_default = _resolve_runtime_port_default_from_inputs(
            settings=settings,
            defaults=defaults,
            runtime_config=persisted_runtime_config,
            host=runtime_host_resolved,
        )
        runtime_port_resolved = resolve_port(
            value=runtime_port,
            interactive=interactive,
            prompt=msg(lang, en="Runtime port", ru="Порт runtime"),
            default=runtime_port_default,
            lang=lang,
        )
        nginx_enabled_resolved = resolve_nginx_enabled(
            value=nginx_enabled,
            interactive=interactive,
            default=read_bool_default(defaults.get("AFKBOT_NGINX_ENABLED"), settings.nginx_enabled),
            lang=lang,
        )
        nginx_port_resolved = resolve_port(
            value=nginx_port,
            interactive=False,
            prompt=msg(lang, en="Nginx port", ru="Порт Nginx"),
            default=read_int_default(defaults.get("AFKBOT_NGINX_PORT"), settings.nginx_port),
            lang=lang,
        )
        nginx_plan = resolve_nginx_plan(
            nginx_enabled=nginx_enabled_resolved,
            runtime_port=runtime_port_resolved,
            api_port=runtime_port_resolved + 1,
            runtime_host_value=nginx_runtime_host,
            api_host_value=nginx_api_host,
            runtime_https_value=nginx_runtime_https,
            api_https_value=nginx_api_https,
            certbot_email_value=certbot_email,
            runtime_host_default=defaults.get("AFKBOT_NGINX_RUNTIME_HOST", ""),
            api_host_default=defaults.get("AFKBOT_NGINX_API_HOST", ""),
            runtime_https_default=read_bool_default(defaults.get("AFKBOT_NGINX_RUNTIME_HTTPS"), False),
            api_https_default=read_bool_default(defaults.get("AFKBOT_NGINX_API_HTTPS"), False),
            certbot_email_default=defaults.get("AFKBOT_CERTBOT_EMAIL", ""),
            interactive=interactive,
            lang=lang,
        )
        auto_install_deps_resolved = True if auto_install_deps is None else auto_install_deps

    return SetupConfig(
        env_file=env_file,
        db_url=db_url_resolved,
        prompt_language=lang.value,
        llm_provider=provider,
        chat_model=model,
        llm_thinking_level=thinking_level_resolved,
        llm_api_key=api_key,
        llm_base_url=base_url,
        custom_interface=custom_interface_resolved,
        llm_proxy_type=proxy_type,
        llm_proxy_url=proxy_url,
        credentials_master_keys=credentials_master_keys,
        runtime_host=runtime_host_resolved,
        runtime_port=runtime_port_resolved,
        nginx_enabled=nginx_enabled_resolved,
        nginx_port=nginx_port_resolved,
        nginx_runtime_host=nginx_plan.runtime_host,
        nginx_runtime_public_port=nginx_plan.runtime_public_port,
        nginx_runtime_https=nginx_plan.runtime_https,
        nginx_api_host=nginx_plan.api_host,
        nginx_api_public_port=nginx_plan.api_public_port,
        nginx_api_https=nginx_plan.api_https,
        certbot_email=nginx_plan.certbot_email,
        public_runtime_url=nginx_plan.public_runtime_url,
        public_chat_api_url=nginx_plan.public_chat_api_url,
        policy_setup_mode=policy_setup_mode_resolved,
        policy_enabled=policy_enabled_resolved,
        policy_preset=policy_preset_resolved,
        policy_confirmation_mode=policy_confirmation_mode_resolved,
        policy_capabilities=policy_capabilities_resolved,
        policy_file_access_mode=policy_file_access_mode_resolved,
        policy_workspace_scope_mode=policy_workspace_scope_mode_resolved,
        policy_allowed_directories=policy_allowed_directories_resolved,
        policy_network_mode=policy_network_mode_resolved,
        policy_network_allowlist=policy_network_allowlist_resolved,
        default_profile_runtime_config=(
            default_profile_runtime_config_override
            or build_default_profile_runtime_config(
                runtime_core=runtime_core,
                base_runtime=default_profile_base_runtime_config,
            )
        ),
        auto_install_deps=auto_install_deps_resolved,
        runtime_secrets_update=dict(resolved_runtime_secrets_update or {}),
    )
