"""Presentation helpers for profile mutation success output."""

from __future__ import annotations

from pathlib import Path

import typer

from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.services.policy import infer_workspace_scope_mode
from afkbot.services.profile_runtime import ProfileDetails
from afkbot.services.setup.policy_inputs import default_policy_network_mode
from afkbot.services.wizard.profile_catalog import infer_profile_scenario_id
from afkbot.services.wizard.preview import build_profile_configuration_preview


def render_profile_mutation_success(
    *,
    profile: ProfileDetails,
    root_dir: Path,
    lang: PromptLanguage,
    verb_en: str,
    verb_ru: str,
) -> None:
    """Print concise interactive success summary for profile create/update."""

    details = profile.model_dump(mode="json")
    effective = details["effective_runtime"]
    typer.echo(
        msg(
            lang,
            en=f"Profile `{details['id']}` {verb_en} successfully.",
            ru=f"Профиль `{details['id']}` успешно {verb_ru}.",
        )
    )
    typer.echo(
        msg(
            lang,
            en=f"Provider/model: {effective['llm_provider']} / {effective['llm_model']}",
            ru=f"Провайдер/модель: {effective['llm_provider']} / {effective['llm_model']}",
        )
    )
    policy = profile.policy
    credential_status = tuple(profile.runtime_secrets.configured_fields) or (
        (
            "provider_api_key_configured"
            if profile.effective_runtime.provider_api_key_configured
            else "provider_api_key_missing"
        ),
    )
    workspace_scope_mode = infer_workspace_scope_mode(
        root_dir=root_dir,
        profile_root=Path(profile.profile_root),
        allowed_directories=policy.allowed_directories,
    )
    network_mode = default_policy_network_mode(
        defaults={"AFKBOT_POLICY_NETWORK_ALLOWLIST": ",".join(policy.network_allowlist)},
        capabilities=policy.capabilities,
    )
    scenario_id = infer_profile_scenario_id(
        capabilities=policy.capabilities,
        file_access_mode=policy.file_access_mode,
        workspace_scope_mode=workspace_scope_mode,
        shell_sandbox_mode=policy.shell_sandbox_mode,
        shell_allowed_commands=policy.shell_allowed_commands,
        network_allowlist=policy.network_allowlist,
    )
    for line in build_profile_configuration_preview(
        scenario_id=scenario_id,
        capabilities=policy.capabilities,
        file_access_mode=policy.file_access_mode,
        workspace_scope_mode=workspace_scope_mode,
        allowed_directories=policy.allowed_directories,
        shell_sandbox_mode=policy.shell_sandbox_mode,
        shell_allowed_commands=policy.shell_allowed_commands,
        network_mode=network_mode,
        network_allowlist=policy.network_allowlist,
        credential_status=credential_status,
        lang=lang,
    ).lines:
        typer.echo(line)
    typer.echo(
        msg(
            lang,
            en=f"Next steps: run `afk chat --profile {details['id']}` or inspect `afk profile show {details['id']}`.",
            ru=f"Дальше: запустите `afk chat --profile {details['id']}` или проверьте `afk profile show {details['id']}`.",
        )
    )


__all__ = ["render_profile_mutation_success"]
