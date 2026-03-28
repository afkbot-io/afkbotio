"""Pure helper tests that support profile CLI mutation flows."""

from __future__ import annotations

from pathlib import Path

from afkbot.cli.commands.profile_mutation_support import (
    build_policy_defaults_from_details,
    resolve_current_runtime_config,
)
from afkbot.services.profile_runtime.contracts import (
    ProfileDetails,
    ProfilePolicyView,
    ProfileRuntimeResolved,
    ProfileRuntimeSecretsView,
)
from afkbot.services.setup.defaults import recommended_policy_capabilities


def test_resolve_current_runtime_config_preserves_scoped_memory_fields_from_effective_runtime() -> None:
    """Fallback runtime reconstruction should keep scoped-memory fields on legacy profiles."""

    # Arrange
    details = ProfileDetails(
        id="default",
        name="Default",
        is_default=True,
        status="active",
        has_runtime_config=False,
        effective_runtime=ProfileRuntimeResolved(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_base_url="https://api.openai.com/v1",
            custom_interface="openai",
            llm_proxy_type="none",
            llm_proxy_url=None,
            llm_thinking_level="high",
            llm_history_turns=12,
            chat_planning_mode="on",
            enabled_tool_plugins=("debug_echo",),
            memory_auto_search_enabled=True,
            memory_auto_search_scope_mode="thread",
            memory_auto_search_limit=5,
            memory_auto_search_include_global=False,
            memory_auto_search_chat_limit=4,
            memory_auto_search_global_limit=1,
            memory_global_fallback_enabled=False,
            memory_auto_context_item_chars=180,
            memory_auto_save_enabled=True,
            memory_auto_save_scope_mode="user_in_chat",
            memory_auto_promote_enabled=True,
            memory_auto_save_kinds=("fact", "decision"),
            memory_auto_save_max_chars=900,
            session_compaction_enabled=True,
            session_compaction_trigger_turns=16,
            session_compaction_keep_recent_turns=8,
            session_compaction_max_chars=5000,
            session_compaction_prune_raw_turns=True,
            provider_api_key_configured=True,
            brave_api_key_configured=False,
        ),
        profile_root="profiles/default",
        system_dir="profiles/default/.system",
        runtime_config=None,
        runtime_config_path="profiles/default/.system/agent_config.json",
        runtime_secrets=ProfileRuntimeSecretsView(
            configured_fields=("openai_api_key",),
            has_profile_secrets=True,
        ),
        runtime_secrets_path="profiles/default/.system/runtime_secrets.enc.json",
        bootstrap_dir="profiles/default/bootstrap",
        skills_dir="profiles/default/skills",
        subagents_dir="profiles/default/subagents",
        policy=ProfilePolicyView(
            enabled=True,
            preset="medium",
            capabilities=("memory",),
            file_access_mode="none",
            allowed_directories=(),
            network_allowlist=(),
        ),
    )

    # Act
    runtime = resolve_current_runtime_config(details)

    # Assert
    assert runtime.memory_auto_search_scope_mode == "thread"
    assert runtime.memory_auto_search_include_global is False
    assert runtime.memory_auto_search_chat_limit == 4
    assert runtime.memory_auto_search_global_limit == 1
    assert runtime.memory_global_fallback_enabled is False
    assert runtime.memory_auto_save_scope_mode == "user_in_chat"
    assert runtime.memory_auto_promote_enabled is True
    assert runtime.memory_auto_save_kinds == ("fact", "decision")


def test_build_policy_defaults_from_details_recognizes_recommended_setup_shape(
    tmp_path: Path,
) -> None:
    """Profile mutation defaults should preserve the recommended setup classification."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    details = ProfileDetails(
        id="default",
        name="Default",
        is_default=True,
        status="active",
        has_runtime_config=False,
        effective_runtime=ProfileRuntimeResolved(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            enabled_tool_plugins=(),
        ),
        profile_root="profiles/default",
        system_dir="profiles/default/.system",
        runtime_config=None,
        runtime_config_path="profiles/default/.system/agent_config.json",
        runtime_secrets=ProfileRuntimeSecretsView(
            configured_fields=("openai_api_key",),
            has_profile_secrets=True,
        ),
        runtime_secrets_path="profiles/default/.system/agent_secrets.json",
        bootstrap_dir="profiles/default/bootstrap",
        skills_dir="profiles/default/skills",
        subagents_dir="profiles/default/subagents",
        policy=ProfilePolicyView(
            enabled=True,
            preset="medium",
            capabilities=recommended_policy_capabilities(),
            file_access_mode="read_write",
            allowed_directories=(str(profile_root.resolve(strict=False)),),
            network_allowlist=("*",),
        ),
    )

    # Act
    defaults = build_policy_defaults_from_details(root_dir=tmp_path, details=details)

    # Assert
    assert defaults["AFKBOT_POLICY_SETUP_MODE"] == "recommended"
