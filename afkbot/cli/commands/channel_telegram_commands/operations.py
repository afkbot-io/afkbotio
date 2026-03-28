"""Read-side and operational helpers for Telegram channel CLI commands."""

from __future__ import annotations

import json

import typer

from afkbot.cli.commands.channel_shared import (
    render_ingress_batch_summary,
    render_reply_humanization_summary,
)
from afkbot.cli.commands.channel_telegram_commands.runtime import TelegramCommandRuntime
from afkbot.cli.commands.inspection_shared import (
    build_channel_inspection_summary,
    render_memory_auto_save_brief,
    render_memory_auto_search_brief,
    render_merge_order_brief,
    render_tool_access_brief,
)


def run_telegram_list(*, runtime: TelegramCommandRuntime, json_output: bool) -> None:
    """List configured Telegram polling endpoints."""

    try:
        channels = runtime.list_endpoints()
    except Exception as exc:
        runtime.raise_error(exc)
        raise AssertionError("unreachable")
    if json_output:
        typer.echo(
            json.dumps(
                {"channels": [item.model_dump(mode="json") for item in channels]},
                ensure_ascii=True,
            )
        )
        return
    if not channels:
        typer.echo("No Telegram channels configured.")
        return
    for item in channels:
        typer.echo(
            f"- {item.endpoint_id}: profile={item.profile_id}, "
            f"credential_profile={item.credential_profile_key}, account_id={item.account_id}, "
            f"group_trigger_mode={item.group_trigger_mode}, tool_profile={item.tool_profile}, "
            f"ingress_batch={render_ingress_batch_summary(item.ingress_batch)}, "
            f"reply_humanization={render_reply_humanization_summary(item.reply_humanization)}, "
            f"enabled={item.enabled}"
        )


def run_telegram_show(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str,
    json_output: bool,
) -> None:
    """Show one Telegram polling endpoint."""

    try:
        channel = runtime.load_endpoint(channel_id)
        state_path = runtime.state_path_for(channel.endpoint_id)
        profile = runtime.load_profile(channel.profile_id)
        inspection = build_channel_inspection_summary(
            settings=runtime.settings,
            profile=profile,
            channel=channel,
        )
    except Exception as exc:
        runtime.raise_error(exc)
        raise AssertionError("unreachable")

    payload = {
        "channel": channel.model_dump(mode="json"),
        "state_path": str(state_path),
        "state_present": state_path.exists(),
        "mutation_state": inspection.mutation_state.model_dump(mode="json"),
        "profile_ceiling": inspection.profile_ceiling.model_dump(mode="json"),
        "effective_permissions": inspection.effective_permissions.model_dump(mode="json"),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=True))
        return
    typer.echo(f"Telegram channel `{channel.endpoint_id}`")
    typer.echo(f"- profile: {channel.profile_id}")
    typer.echo(f"- credential_profile: {channel.credential_profile_key}")
    typer.echo(f"- account_id: {channel.account_id}")
    typer.echo(f"- merge_order: {render_merge_order_brief()}")
    typer.echo(f"- inherited_defaults_source: {inspection.mutation_state.inherited_defaults_source}")
    typer.echo("- current_channel_overrides: " + (", ".join(inspection.mutation_state.current_override_fields) or "none"))
    typer.echo("- profile_ceiling_tool_access: " + render_tool_access_brief(inspection.profile_ceiling.tool_access))
    typer.echo(f"- group_trigger_mode: {channel.group_trigger_mode}")
    typer.echo(f"- tool_profile: {channel.tool_profile}")
    typer.echo(f"- ingress_batch.enabled: {channel.ingress_batch.enabled}")
    typer.echo(f"- ingress_batch.debounce_ms: {channel.ingress_batch.debounce_ms}")
    typer.echo(f"- ingress_batch.cooldown_sec: {channel.ingress_batch.cooldown_sec}")
    typer.echo(f"- ingress_batch.max_batch_size: {channel.ingress_batch.max_batch_size}")
    typer.echo(f"- ingress_batch.max_buffer_chars: {channel.ingress_batch.max_buffer_chars}")
    typer.echo("- effective_memory_auto_search: " + render_memory_auto_search_brief(inspection.effective_permissions.memory_behavior))
    typer.echo("- effective_memory_auto_save: " + render_memory_auto_save_brief(inspection.effective_permissions.memory_behavior))
    typer.echo(
        "- effective_memory_cross_chat_access: "
        + inspection.effective_permissions.memory_behavior.explicit_cross_chat_access
    )
    typer.echo(f"- reply_humanization.enabled: {channel.reply_humanization.enabled}")
    typer.echo(f"- reply_humanization.min_delay_ms: {channel.reply_humanization.min_delay_ms}")
    typer.echo(f"- reply_humanization.max_delay_ms: {channel.reply_humanization.max_delay_ms}")
    typer.echo(f"- reply_humanization.chars_per_second: {channel.reply_humanization.chars_per_second}")
    typer.echo(f"- enabled: {channel.enabled}")
    typer.echo(f"- state_path: {state_path}")
    typer.echo(f"- state_present: {state_path.exists()}")


def run_telegram_delete(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str,
    keep_binding: bool,
    json_output: bool,
) -> None:
    """Delete one Telegram polling endpoint and its saved offset state."""

    try:
        runtime.load_endpoint(channel_id)
        runtime.delete_endpoint(channel_id)
        binding_removed = False if keep_binding else runtime.delete_binding(channel_id)
    except Exception as exc:
        runtime.raise_error(exc)
        raise AssertionError("unreachable")

    if json_output:
        typer.echo(json.dumps({"ok": True, "binding_removed": binding_removed}, ensure_ascii=True))
        runtime.reload_notice(runtime.settings)
        return
    typer.echo(f"Telegram channel `{channel_id}` deleted.")
    if binding_removed:
        typer.echo(f"Matching binding `{channel_id}` deleted.")
    runtime.reload_notice(runtime.settings)


def run_telegram_status(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str | None,
    probe: bool,
    json_output: bool,
) -> None:
    """Show Telegram polling endpoint status."""

    try:
        payload = runtime.status_payload(channel_id, probe)
    except Exception as exc:
        runtime.raise_error(exc)
        raise AssertionError("unreachable")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=True))
    else:
        runtime.render_status_payload(payload)
    if payload.get("ok") is False:
        raise typer.Exit(code=1)


def run_telegram_poll_once(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str,
    json_output: bool,
) -> None:
    """Run one polling iteration and process one Telegram update batch."""

    payload = runtime.poll_once_payload(channel_id)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=True))
    else:
        runtime.render_poll_once_payload(channel_id, payload)
    if payload.get("ok") is False:
        raise typer.Exit(code=1)


def run_telegram_reset_offset(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str,
    json_output: bool,
) -> None:
    """Reset saved Telegram polling offset state."""

    payload = runtime.reset_offset_payload(channel_id)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=True))
        runtime.reload_notice(runtime.settings)
        return
    typer.echo(
        f"Offset state for `{channel_id}` {'removed' if payload['removed'] else 'not found'}: {payload['state_path']}"
    )
    runtime.reload_notice(runtime.settings)


__all__ = [
    "run_telegram_delete",
    "run_telegram_list",
    "run_telegram_poll_once",
    "run_telegram_reset_offset",
    "run_telegram_show",
    "run_telegram_status",
]
