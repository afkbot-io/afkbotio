"""Interactive setup command for default-profile initialization."""

from __future__ import annotations

from pathlib import Path

import typer

from afkbot.cli.managed_runtime import reload_install_managed_runtime_notice
from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.profile_mutation_support import (
    collect_setup_profile_inputs,
)
from afkbot.cli.commands.setup_support import (
    format_setup_runtime_error,
    load_current_default_profile,
    load_setup_defaults,
)
from afkbot.cli.presentation.activity_indicator import ActivityIndicator
from afkbot.cli.presentation.prompt_i18n import detect_system_prompt_language
from afkbot.cli.presentation.setup_prompts import (
    normalize_prompt_language,
)
from afkbot.services.setup.config_resolver import (
    collect_setup_config,
    ensure_security_ack,
)
from afkbot.services.setup.command_runtime import (
    execute_setup_runtime,
    render_setup_success,
)
from afkbot.services.profile_runtime import (
    ProfileServiceError,
    get_profile_runtime_secrets_service,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register setup command in Typer app."""

    @app.command("setup")
    def setup(
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Use defaults/flags without interactive questions.",
        ),
        bootstrap_only: bool = typer.Option(
            False,
            "--bootstrap-only",
            hidden=True,
            help="Internal flag: seed platform runtime store after installer bootstrap.",
        ),
        accept_risk: bool = typer.Option(
            False,
            "--accept-risk",
            help="Acknowledge responsibility for secrets and automations.",
        ),
        lang: str | None = typer.Option(
            None,
            "--lang",
            help="Interactive setup language: en or ru.",
        ),
        ru: bool = typer.Option(
            False,
            "--ru",
            help="Shortcut for --lang ru in interactive setup.",
        ),
        llm_provider: str | None = typer.Option(
            None,
            "--llm-provider",
            help="Default chat provider: openrouter, openai, claude, moonshot, deepseek, xai, qwen, or custom.",
        ),
        chat_model: str | None = typer.Option(
            None,
            "--chat-model",
            help="Default chat model id for the selected provider.",
        ),
        thinking_level: str | None = typer.Option(
            None,
            "--thinking-level",
            help="Default reasoning budget for the default profile: low, medium, high, or very_high.",
        ),
        llm_api_key_file: Path | None = typer.Option(
            None,
            "--llm-api-key-file",
            "--openrouter-api-key-file",
            dir_okay=False,
            file_okay=True,
            exists=True,
            help="Path to file containing provider API key.",
        ),
        llm_base_url: str | None = typer.Option(
            None,
            "--llm-base-url",
            "--openrouter-base-url",
            help="Base URL override for the selected provider API.",
        ),
        custom_interface: str | None = typer.Option(
            None,
            "--custom-interface",
            help="API interface for custom providers. Currently only `openai` is supported.",
        ),
        skip_llm_token_verify: bool = typer.Option(
            False,
            "--skip-llm-token-verify",
            help="Skip provider API token verification (not recommended).",
        ),
        llm_proxy_type: str | None = typer.Option(
            None,
            "--llm-proxy-type",
            help="Outbound proxy type for provider requests: none, http, socks5, socks5h.",
        ),
        llm_proxy_url: str | None = typer.Option(
            None,
            "--llm-proxy-url",
            help="Outbound proxy URL for provider requests.",
        ),
        policy_enabled: bool | None = typer.Option(
            None,
            "--policy-enabled/--policy-disabled",
            help="Enable or disable runtime policy restrictions for the default profile.",
        ),
        policy_preset: str | None = typer.Option(
            None,
            "--policy-preset",
            help="Policy baseline: simple, medium, or strict.",
        ),
        policy_capability: list[str] = typer.Option(
            [],
            "--policy-capability",
            help=(
                "Capability id (repeatable): "
                "files,shell,memory,credentials,subagents,automation,http,web,browser,skills,apps,debug "
                "(legacy aliases: email,telegram)"
            ),
        ),
        policy_file_access_mode: str | None = typer.Option(
            None,
            "--policy-file-access-mode",
            help="File access mode: none, read_only, or read_write.",
        ),
        policy_workspace_scope: str | None = typer.Option(
            None,
            "--policy-workspace-scope",
            help="Workspace scope: profile_only, project_only, profile_and_project, full_system, or custom.",
        ),
        policy_network_host: list[str] = typer.Option(
            [],
            "--policy-network-host",
            help=(
                "Allowed outbound host/domain for runtime policy (repeatable). "
                "Examples: api.search.brave.com, api.telegram.org, smtp.gmail.com"
            ),
        ),
        auto_install_deps: bool | None = typer.Option(
            None,
            "--auto-install-deps/--no-auto-install-deps",
            hidden=True,
            help="Allow or forbid automatic installation of missing system dependencies during bootstrap-only seeding.",
        ),
    ) -> None:
        """Configure or reconfigure the default profile after the public installer bootstraps the platform."""

        settings = get_settings()
        settings.root_dir.mkdir(parents=True, exist_ok=True)
        runtime_env_file = settings.root_dir / ".unused"
        defaults = load_setup_defaults(settings)
        interactive = not yes
        current_default_details = load_current_default_profile(settings)
        current_default_runtime_secrets = (
            get_profile_runtime_secrets_service(settings).load("default")
            if current_default_details is not None
            else None
        )
        try:
            default_prompt_language = (
                lang
                or defaults.get("AFKBOT_PROMPT_LANGUAGE")
                or detect_system_prompt_language().value
            )
            prompt_language = normalize_prompt_language(
                value=default_prompt_language,
                ru=ru,
            )
        except ValueError as exc:
            raise_usage_error(str(exc))

        if bootstrap_only:
            interactive = False
            setup_profile_inputs = None
        else:
            ensure_security_ack(
                interactive=interactive,
                accept_risk=accept_risk,
                lang=prompt_language,
            )
            setup_profile_inputs = collect_setup_profile_inputs(
                settings=settings,
                interactive=interactive,
                lang=prompt_language,
                current_details=current_default_details,
                current_runtime_secrets=current_default_runtime_secrets,
                defaults=defaults,
                llm_provider=llm_provider,
                chat_model=chat_model,
                thinking_level=thinking_level,
                llm_api_key_file=llm_api_key_file,
                llm_base_url=llm_base_url,
                custom_interface=custom_interface,
                llm_proxy_type=llm_proxy_type,
                llm_proxy_url=llm_proxy_url,
                policy_enabled=policy_enabled,
                policy_preset=policy_preset,
                policy_capability=tuple(policy_capability),
                policy_file_access_mode=policy_file_access_mode,
                policy_workspace_scope=policy_workspace_scope,
                policy_network_host=tuple(policy_network_host),
            )
        config = collect_setup_config(
            settings=settings,
            defaults=defaults,
            env_file=runtime_env_file,
            interactive=interactive,
            lang=prompt_language,
            llm_provider=(
                setup_profile_inputs.runtime_core.llm_provider
                if setup_profile_inputs is not None
                else llm_provider
            ),
            chat_model=(
                setup_profile_inputs.runtime_core.llm_model
                if setup_profile_inputs is not None
                else chat_model
            ),
            thinking_level=(
                setup_profile_inputs.runtime_core.llm_thinking_level
                if setup_profile_inputs is not None
                else thinking_level
            ),
            resolved_api_key=(
                setup_profile_inputs.provider_api_key if setup_profile_inputs is not None else None
            ),
            llm_api_key_file=llm_api_key_file,
            llm_base_url=(
                setup_profile_inputs.runtime_core.llm_base_url
                if setup_profile_inputs is not None
                else llm_base_url
            ),
            custom_interface=(
                setup_profile_inputs.runtime_core.custom_interface
                if setup_profile_inputs is not None
                else custom_interface
            ),
            skip_llm_token_verify=skip_llm_token_verify,
            llm_proxy_type=(
                setup_profile_inputs.runtime_core.llm_proxy_type
                if setup_profile_inputs is not None
                else llm_proxy_type
            ),
            llm_proxy_url=(
                setup_profile_inputs.runtime_core.llm_proxy_url
                if setup_profile_inputs is not None
                else llm_proxy_url
            ),
            runtime_host=None,
            runtime_port=None,
            nginx_enabled=None,
            nginx_port=None,
            nginx_runtime_host=None,
            nginx_runtime_https=None,
            nginx_api_host=None,
            nginx_api_https=None,
            certbot_email=None,
            policy_enabled=policy_enabled,
            policy_preset=policy_preset,
            policy_capability=tuple(policy_capability),
            policy_file_access_mode=policy_file_access_mode,
            policy_workspace_scope=policy_workspace_scope,
            policy_network_host=tuple(policy_network_host),
            auto_install_deps=auto_install_deps if bootstrap_only else None,
            resolved_runtime_core=(
                setup_profile_inputs.runtime_core if setup_profile_inputs is not None else None
            ),
            resolved_policy_inputs=(
                setup_profile_inputs.resolved_policy if setup_profile_inputs is not None else None
            ),
            default_profile_runtime_config_override=(
                setup_profile_inputs.runtime_config if setup_profile_inputs is not None else None
            ),
            profile_setup_only=not bootstrap_only,
            platform_seed_only=bootstrap_only,
        )
        try:
            response = execute_setup_runtime(
                settings=settings,
                config=config,
                interactive=interactive,
                bootstrap_only=bootstrap_only,
                prompt_language=prompt_language,
                activity_indicator_cls=ActivityIndicator,
            )
            if interactive:
                render_setup_success(
                    interactive=True,
                    prompt_language=prompt_language,
                    response=response,
                )
            else:
                import json

                typer.echo(json.dumps(response, ensure_ascii=True))
            if not bootstrap_only:
                reload_install_managed_runtime_notice(settings)
        except ProfileServiceError as exc:
            raise_usage_error(format_setup_runtime_error(exc))
        except (OSError, RuntimeError) as exc:
            raise_usage_error(format_setup_runtime_error(exc))
        except ValueError as exc:
            raise_usage_error(str(exc))
