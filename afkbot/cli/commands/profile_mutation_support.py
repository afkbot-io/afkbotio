"""Compatibility facade for shared profile mutation CLI helpers."""

from __future__ import annotations

import typer

from afkbot.cli.commands.profile_mutation import (
    CollectedProfileMutationInputs,
    ResolvedProfilePolicyInputs,
    ResolvedProfileRuntimeCore,
    build_policy_defaults_from_details,
    build_profile_defaults,
    build_runtime_defaults_from_details,
    collect_profile_mutation_inputs,
    collect_setup_profile_inputs,
    render_profile_mutation_success,
    resolve_current_runtime_config,
)
from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.llm.token_verifier import verify_provider_token
from afkbot.services.profile_runtime import provider_secret_field
from afkbot.services.setup.profile_resolution import (
    build_profile_runtime_config,
    normalize_policy_file_access_mode_value,
    normalize_policy_workspace_scope_mode_value,
    resolve_policy_allowed_directories,
    resolve_policy_allowed_directories_from_scope,
    resolve_profile_policy_inputs,
    resolve_profile_runtime_core,
    resolve_profile_thinking_level,
)

__all__ = [
    "CollectedProfileMutationInputs",
    "ResolvedProfilePolicyInputs",
    "ResolvedProfileRuntimeCore",
    "build_policy_defaults_from_details",
    "build_profile_defaults",
    "build_profile_runtime_config",
    "build_runtime_defaults_from_details",
    "collect_profile_mutation_inputs",
    "collect_setup_profile_inputs",
    "normalize_policy_file_access_mode_value",
    "normalize_policy_workspace_scope_mode_value",
    "render_profile_mutation_success",
    "resolve_current_runtime_config",
    "resolve_policy_allowed_directories",
    "resolve_policy_allowed_directories_from_scope",
    "resolve_profile_policy_inputs",
    "resolve_profile_runtime_core",
    "resolve_profile_thinking_level",
    "verify_profile_provider_token",
]


def verify_profile_provider_token(
    *,
    provider_id: LLMProviderId,
    base_url: str,
    proxy_type: str,
    proxy_url: str,
    runtime_secrets: dict[str, str],
    skip_verify: bool,
) -> None:
    """Verify the effective provider token when profile mutation collected or reused one."""

    if skip_verify:
        return
    effective_key = (
        runtime_secrets.get(provider_secret_field(provider_id.value))
        or runtime_secrets.get("llm_api_key")
        or ""
    ).strip()
    if not effective_key:
        return
    verification = verify_provider_token(
        provider_id=provider_id,
        api_key=effective_key,
        base_url=base_url,
        proxy_url=proxy_url if proxy_type != "none" else None,
    )
    if verification.ok:
        return
    raise typer.BadParameter(verification.reason or "LLM token verification failed.")
