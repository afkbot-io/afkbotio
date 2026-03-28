"""Profile inspection CLI commands."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.commands.profile_common import emit_profile_error
from afkbot.cli.commands.inspection_shared import (
    build_linked_channel_inspection_summary,
    build_linked_channel_summary,
    build_profile_mutation_state_summary,
    build_profile_permission_summary,
    render_memory_auto_save_brief,
    render_memory_auto_search_brief,
    render_merge_order_brief,
    render_tool_access_brief,
)
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.profile_runtime import ProfileServiceError, get_profile_service
from afkbot.settings import get_settings


def register_read(profile_app: typer.Typer) -> None:
    """Register `afk profile list|show`."""

    @profile_app.command("list")
    def list_profiles(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """List configured runtime profiles."""

        settings = get_settings()
        try:
            profiles = asyncio.run(get_profile_service(settings).list())
        except ProfileServiceError as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        if json_output:
            typer.echo(
                json.dumps(
                    {"profiles": [item.model_dump(mode="json") for item in profiles]},
                    ensure_ascii=True,
                )
            )
            return
        if not profiles:
            typer.echo("No profiles configured.")
            return
        for item in profiles:
            typer.echo(
                f"- {item.id}: name={item.name}, "
                f"provider={item.effective_runtime.llm_provider}, "
                f"model={item.effective_runtime.llm_model}"
            )

    @profile_app.command("show")
    def show(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show one profile with runtime config and policy details."""

        settings = get_settings()
        try:
            profile = asyncio.run(get_profile_service(settings).get(profile_id=validate_profile_id(profile_id)))
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        linked_channels = asyncio.run(
            get_channel_endpoint_service(settings).list(profile_id=profile.id)
        )
        linked_channel_inspections = [
            build_linked_channel_inspection_summary(
                settings=settings,
                profile=profile,
                channel=item,
            )
            for item in linked_channels
        ]
        mutation_state = build_profile_mutation_state_summary(profile)
        effective_permissions = build_profile_permission_summary(
            settings=settings,
            profile=profile,
        )
        payload = {
            "profile": profile.model_dump(mode="json"),
            "mutation_state": mutation_state.model_dump(mode="json"),
            "linked_channels": [build_linked_channel_summary(item).model_dump(mode="json") for item in linked_channels],
            "linked_channel_inspections": [
                item.model_dump(mode="json") for item in linked_channel_inspections
            ],
            "effective_permissions": effective_permissions.model_dump(mode="json"),
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        memory_behavior = effective_permissions.memory_behavior
        typer.echo(f"Profile `{profile.id}`")
        typer.echo(f"- name: {profile.name}")
        typer.echo(
            f"- provider/model: {profile.effective_runtime.llm_provider} / {profile.effective_runtime.llm_model}"
        )
        typer.echo(f"- profile_root: {profile.profile_root}")
        typer.echo(f"- merge_order: {render_merge_order_brief()}")
        typer.echo(f"- inherited_defaults_source: {mutation_state.inherited_defaults_source}")
        typer.echo(
            "- current_runtime_overrides: "
            + (
                ", ".join(
                    field.removeprefix("runtime.")
                    for field in mutation_state.current_override_fields
                    if field.startswith("runtime.")
                )
                or "none"
            )
        )
        typer.echo(
            "- current_policy_state: "
            + (
                f"enabled={profile.policy.enabled}, "
                f"preset={profile.policy.preset}, "
                "capabilities="
                + (",".join(profile.policy.capabilities) or "none")
            )
        )
        typer.echo(f"- policy_enabled: {effective_permissions.policy_enabled}")
        typer.echo(f"- policy_preset: {effective_permissions.policy_preset}")
        typer.echo(
            "- capabilities: "
            + (", ".join(effective_permissions.capability_ids) or "none")
        )
        typer.echo(f"- default_workspace_root: {effective_permissions.default_workspace_root}")
        typer.echo(f"- shell_default_cwd: {effective_permissions.shell_default_cwd}")
        typer.echo(f"- file_scope_mode: {effective_permissions.file_scope_mode}")
        typer.echo(f"- file_access: {effective_permissions.file_access_mode}")
        typer.echo(f"- network_access: {effective_permissions.network_access}")
        typer.echo(f"- tool_access: {render_tool_access_brief(effective_permissions.tool_access)}")
        typer.echo("- memory_auto_search: " + render_memory_auto_search_brief(memory_behavior))
        typer.echo("- memory_auto_save: " + render_memory_auto_save_brief(memory_behavior))
        typer.echo(
            "- linked_channels: "
            + ("none" if not linked_channels else str(len(linked_channels)))
        )
        for item in linked_channels:
            summary = build_linked_channel_summary(item)
            inspection = next(
                entry for entry in linked_channel_inspections if entry.channel.endpoint_id == summary.endpoint_id
            )
            typer.echo(
                f"  - {summary.endpoint_id}: transport={summary.transport}, "
                f"account_id={summary.account_id}, enabled={summary.enabled}, mode={summary.mode}, "
                f"tool_profile={inspection.channel_guardrails.channel_tool_profile}"
            )
            typer.echo(
                "    effective_tools="
                + ",".join(
                    (
                        f"files={inspection.effective_permissions.tool_access.files}",
                        f"shell={inspection.effective_permissions.tool_access.shell}",
                        f"memory={inspection.effective_permissions.tool_access.memory}",
                        f"credentials={inspection.effective_permissions.tool_access.credentials}",
                        f"apps={inspection.effective_permissions.tool_access.apps}",
                    )
                )
            )
