"""Profile creation CLI command."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.commands.profile_common import emit_profile_error, resolve_profile_id
from afkbot.cli.commands.policy_option_help import policy_capability_option_help
from afkbot.cli.commands.profile_mutation_support import (
    build_profile_defaults,
    collect_profile_mutation_inputs,
    render_profile_mutation_success,
    verify_profile_provider_token,
)
from afkbot.cli.presentation.setup_prompts import PromptLanguage, resolve_prompt_language
from afkbot.services.setup.defaults import load_env_defaults
from afkbot.services.profile_id import InvalidProfileIdError
from afkbot.services.profile_runtime import (
    ProfileServiceError,
    get_profile_service,
)
from afkbot.settings import get_settings


def register_add(profile_app: typer.Typer) -> None:
    """Register `afk profile add`."""

    @profile_app.command("add")
    def add(
        profile_id_arg: str | None = typer.Argument(None, help="Runtime profile id."),
        profile_id: str | None = typer.Option(None, "--id", help="Runtime profile id."),
        name: str | None = typer.Option(None, "--name", help="Human-readable profile name."),
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Use defaults/flags without interactive questions.",
        ),
        lang: str | None = typer.Option(
            None,
            "--lang",
            help="Interactive language: en or ru.",
        ),
        ru: bool = typer.Option(
            False,
            "--ru",
            help="Shortcut for --lang ru in interactive mode.",
        ),
        llm_provider: str | None = typer.Option(
            None,
            "--llm-provider",
            help=(
                "Chat provider: openrouter, openai, openai-codex, claude, moonshot, deepseek, "
                "xai, qwen, minimax-portal, github-copilot, or custom."
            ),
        ),
        chat_model: str | None = typer.Option(
            None,
            "--chat-model",
            help="Chat model id for this profile.",
        ),
        llm_base_url: str | None = typer.Option(
            None,
            "--llm-base-url",
            help="Base URL override for this profile provider.",
        ),
        custom_interface: str | None = typer.Option(
            None,
            "--custom-interface",
            help="API interface for custom providers. Currently only `openai` is supported.",
        ),
        minimax_region: str | None = typer.Option(
            None,
            "--minimax-region",
            help="MiniMax OAuth region for minimax-portal: global or cn.",
        ),
        llm_proxy_type: str | None = typer.Option(
            None,
            "--llm-proxy-type",
            help="Outbound proxy type: none, http, socks5, socks5h.",
        ),
        llm_proxy_url: str | None = typer.Option(
            None,
            "--llm-proxy-url",
            help="Outbound proxy URL for provider requests.",
        ),
        llm_api_key: str | None = typer.Option(
            None,
            "--llm-api-key",
            help="Optional generic provider credential fallback (API key or OAuth token) for this profile.",
            hide_input=True,
        ),
        provider_api_key: str | None = typer.Option(
            None,
            "--provider-api-key",
            help="Optional provider-specific credential (API key or OAuth token) stored only for this profile.",
            hide_input=True,
        ),
        skip_llm_token_verify: bool = typer.Option(
            False,
            "--skip-llm-token-verify",
            help="Skip provider API token verification for this profile setup.",
        ),
        brave_api_key: str | None = typer.Option(
            None,
            "--brave-api-key",
            help="Optional Brave Search API key stored only for this profile.",
            hide_input=True,
        ),
        thinking_level: str | None = typer.Option(
            None,
            "--thinking-level",
            help="Default reasoning budget for this profile: low, medium, high, or very_high.",
        ),
        llm_history_turns: int | None = typer.Option(
            None,
            "--llm-history-turns",
            help="Number of previous chat turns to replay into the model for this profile.",
        ),
        planning_mode: str | None = typer.Option(
            None,
            "--planning-mode",
            help="Default chat planning mode for this profile: off, auto, or on.",
        ),
        tool_plugin: list[str] = typer.Option(
            [],
            "--tool-plugin",
            help="Optional repeatable tool plugin allowlist for this profile.",
        ),
        memory_auto_search_enabled: bool | None = typer.Option(
            None,
            "--memory-auto-search-enabled/--memory-auto-search-disabled",
            help="Enable or disable automatic semantic memory search before turns.",
        ),
        memory_auto_search_scope_mode: str | None = typer.Option(
            None,
            "--memory-auto-search-scope-mode",
            help="Automatic memory search scope mode: auto, profile, chat, thread, or user_in_chat.",
        ),
        memory_auto_search_limit: int | None = typer.Option(
            None,
            "--memory-auto-search-limit",
            help="Maximum semantic memory hits injected into one turn.",
        ),
        memory_auto_search_include_global: bool | None = typer.Option(
            None,
            "--memory-auto-search-include-global/--memory-auto-search-local-only",
            help="Append promoted_global profile memory after local scoped hits.",
        ),
        memory_auto_search_chat_limit: int | None = typer.Option(
            None,
            "--memory-auto-search-chat-limit",
            help="Maximum local chat-scope semantic memory hits per turn.",
        ),
        memory_auto_search_global_limit: int | None = typer.Option(
            None,
            "--memory-auto-search-global-limit",
            help="Maximum promoted_global fallback semantic memory hits per turn.",
        ),
        memory_global_fallback_enabled: bool | None = typer.Option(
            None,
            "--memory-global-fallback-enabled/--memory-global-fallback-disabled",
            help="Enable or disable promoted_global profile fallback during automatic local memory search.",
        ),
        memory_auto_context_item_chars: int | None = typer.Option(
            None,
            "--memory-auto-context-item-chars",
            help="Maximum chars kept per automatic memory hit in turn context.",
        ),
        memory_auto_save_enabled: bool | None = typer.Option(
            None,
            "--memory-auto-save-enabled/--memory-auto-save-disabled",
            help="Enable or disable automatic semantic memory extraction after finalized turns.",
        ),
        memory_auto_save_scope_mode: str | None = typer.Option(
            None,
            "--memory-auto-save-scope-mode",
            help="Automatic memory save scope mode: auto, profile, chat, thread, or user_in_chat.",
        ),
        memory_auto_promote_enabled: bool | None = typer.Option(
            None,
            "--memory-auto-promote-enabled/--memory-auto-promote-disabled",
            help="Enable or disable automatic promotion heuristics from local chat memory to profile-global memory.",
        ),
        memory_auto_save_kind: list[str] = typer.Option(
            [],
            "--memory-auto-save-kind",
            help="Repeatable semantic kind allowed for automatic memory extraction.",
        ),
        memory_auto_save_max_chars: int | None = typer.Option(
            None,
            "--memory-auto-save-max-chars",
            help="Maximum chars stored per automatically saved memory record.",
        ),
        session_compaction_enabled: bool | None = typer.Option(
            None,
            "--session-compaction-enabled/--session-compaction-disabled",
            help="Enable or disable session transcript compaction for this profile.",
        ),
        session_compaction_trigger_turns: int | None = typer.Option(
            None,
            "--session-compaction-trigger-turns",
            help="Refresh session summary when raw session turns exceed this threshold.",
        ),
        session_compaction_keep_recent_turns: int | None = typer.Option(
            None,
            "--session-compaction-keep-recent-turns",
            help="Keep this many recent raw turns outside the compacted summary.",
        ),
        session_compaction_max_chars: int | None = typer.Option(
            None,
            "--session-compaction-max-chars",
            help="Maximum stored chars for one compacted session summary.",
        ),
        session_compaction_prune_raw_turns: bool | None = typer.Option(
            None,
            "--session-compaction-prune-raw-turns/--session-compaction-keep-raw-turns",
            help="Delete compacted raw turns after trusted session summary is persisted.",
        ),
        policy_enabled: bool | None = typer.Option(
            None,
            "--policy-enabled/--policy-disabled",
            help="Enable or disable runtime policy restrictions for this profile.",
        ),
        policy_preset: str | None = typer.Option(
            None,
            "--policy-preset",
            help="Policy baseline: simple, medium, or strict.",
        ),
        policy_capability: list[str] = typer.Option(
            [],
            "--policy-capability",
            help=policy_capability_option_help(),
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
        policy_allowed_dir: list[str] = typer.Option(
            [],
            "--policy-allowed-dir",
            help="Repeatable custom file access allowlist directory for this profile.",
        ),
        policy_network_host: list[str] = typer.Option(
            [],
            "--policy-network-host",
            help="Allowed outbound host/domain for runtime policy (repeatable).",
        ),
    ) -> None:
        """Create one profile with profile-scoped runtime settings and policy."""

        settings = get_settings()
        defaults = build_profile_defaults(load_env_defaults(settings=settings))
        interactive = not yes
        try:
            prompt_language: PromptLanguage = resolve_prompt_language(
                settings=settings,
                value=(lang or defaults.get("AFKBOT_PROMPT_LANGUAGE")),
                ru=ru,
            )
            resolved_profile_id = resolve_profile_id(
                value=profile_id or profile_id_arg,
                interactive=interactive,
                lang=prompt_language,
            )
            mutation_inputs = collect_profile_mutation_inputs(
                settings=settings,
                interactive=interactive,
                lang=prompt_language,
                profile_id=resolved_profile_id,
                current_details=None,
                defaults=defaults,
                name=name,
                llm_provider=llm_provider,
                chat_model=chat_model,
                thinking_level=thinking_level,
                llm_base_url=llm_base_url,
                custom_interface=custom_interface,
                llm_proxy_type=llm_proxy_type,
                llm_proxy_url=llm_proxy_url,
                llm_api_key_file=None,
                llm_api_key=llm_api_key,
                provider_api_key=provider_api_key,
                minimax_region=minimax_region,
                planning_mode=planning_mode,
                current_runtime_secrets=None,
                policy_enabled=policy_enabled,
                policy_preset=policy_preset,
                policy_capability=tuple(policy_capability),
                policy_file_access_mode=policy_file_access_mode,
                policy_workspace_scope=policy_workspace_scope,
                policy_allowed_dir=tuple(policy_allowed_dir),
                policy_network_host=tuple(policy_network_host),
                llm_history_turns=llm_history_turns,
                tool_plugins=tuple(tool_plugin),
                memory_auto_search_enabled=memory_auto_search_enabled,
                memory_auto_search_scope_mode=memory_auto_search_scope_mode,
                memory_auto_search_limit=memory_auto_search_limit,
                memory_auto_search_include_global=memory_auto_search_include_global,
                memory_auto_search_chat_limit=memory_auto_search_chat_limit,
                memory_auto_search_global_limit=memory_auto_search_global_limit,
                memory_global_fallback_enabled=memory_global_fallback_enabled,
                memory_auto_context_item_chars=memory_auto_context_item_chars,
                memory_auto_save_enabled=memory_auto_save_enabled,
                memory_auto_save_scope_mode=memory_auto_save_scope_mode,
                memory_auto_promote_enabled=memory_auto_promote_enabled,
                memory_auto_save_kinds=tuple(memory_auto_save_kind),
                memory_auto_save_max_chars=memory_auto_save_max_chars,
                session_compaction_enabled=session_compaction_enabled,
                session_compaction_trigger_turns=session_compaction_trigger_turns,
                session_compaction_keep_recent_turns=session_compaction_keep_recent_turns,
                session_compaction_max_chars=session_compaction_max_chars,
                session_compaction_prune_raw_turns=session_compaction_prune_raw_turns,
            )
            runtime_secrets = dict(mutation_inputs.runtime_secrets_update)
            brave_secret = (brave_api_key or "").strip()
            if brave_secret:
                runtime_secrets["brave_api_key"] = brave_secret
            verify_profile_provider_token(
                provider_id=mutation_inputs.runtime_core.provider_id,
                base_url=mutation_inputs.runtime_core.llm_base_url,
                proxy_type=mutation_inputs.runtime_core.llm_proxy_type,
                proxy_url=mutation_inputs.runtime_core.llm_proxy_url,
                runtime_secrets={
                    **runtime_secrets,
                    "llm_api_key": mutation_inputs.provider_api_key,
                },
                skip_verify=skip_llm_token_verify,
            )
            profile = asyncio.run(
                get_profile_service(settings).create(
                    profile_id=resolved_profile_id,
                    name=mutation_inputs.resolved_name,
                    runtime_config=mutation_inputs.runtime_config,
                    runtime_secrets=runtime_secrets or None,
                    policy_enabled=mutation_inputs.resolved_policy.enabled,
                    policy_preset=mutation_inputs.resolved_policy.preset,
                    policy_capabilities=mutation_inputs.resolved_policy.capabilities,
                    policy_file_access_mode=mutation_inputs.resolved_policy.file_access_mode,
                    policy_allowed_directories=mutation_inputs.resolved_policy.allowed_directories or None,
                    policy_network_allowlist=mutation_inputs.resolved_policy.network_allowlist,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None

        if interactive:
            render_profile_mutation_success(
                profile=profile,
                lang=prompt_language,
                verb_en="created",
                verb_ru="создан",
            )
            return
        typer.echo(json.dumps({"profile": profile.model_dump(mode="json")}, ensure_ascii=True))
