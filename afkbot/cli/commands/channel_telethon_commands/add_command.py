"""Typer wiring for `afk channel telethon add`."""

from __future__ import annotations

import json

import typer

from afkbot.cli.commands.channel_telethon_commands.add_support import create_telethon_channel
from afkbot.cli.commands.channel_telethon_commands.legacy import (
    raise_legacy_telethon_channel_error,
    reload_legacy_managed_runtime_notice,
)
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channels.tool_profiles import CHANNEL_TOOL_PROFILE_HELP
from afkbot.settings import get_settings


def register_telethon_add_command(telethon_app: typer.Typer) -> None:
    """Register the Telethon create command under the given Typer app."""

    @telethon_app.command("add")
    def telethon_add(
        channel_id: str | None = typer.Argument(None, help="Stable channel endpoint id."),
        profile_id: str | None = typer.Option(None, "--profile", help="Target profile-agent id."),
        credential_profile_key: str | None = typer.Option(
            None,
            "--credential-profile",
            help="Telethon credential profile key holding api_id/api_hash/session_string.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Channel account id used by bindings and routing telemetry.",
        ),
        enabled: bool | None = typer.Option(None, "--enabled/--disabled", help="Enable or disable this endpoint."),
        reply_mode: str | None = typer.Option(
            None,
            "--reply-mode",
            help="Outbound reply mode: disabled, same_chat.",
        ),
        tool_profile: str | None = typer.Option(
            None,
            "--tool-profile",
            help=CHANNEL_TOOL_PROFILE_HELP,
            case_sensitive=False,
        ),
        reply_blocked_chat_patterns: str | None = typer.Option(
            None,
            "--reply-blocked-chat-patterns",
            help="Comma-separated case-insensitive contact name, username, or chat title substrings to suppress reactive replies.",
        ),
        reply_allowed_chat_patterns: str | None = typer.Option(
            None,
            "--reply-allowed-chat-patterns",
            help="Comma-separated case-insensitive contact name, username, or chat title substrings allowlist for reactive replies.",
        ),
        group_invocation_mode: str | None = typer.Option(
            None,
            "--group-invocation-mode",
            help="Group trigger mode: reply_or_command, reply_only, command_only, all_messages.",
        ),
        process_self_commands: bool | None = typer.Option(
            None,
            "--process-self-commands/--no-process-self-commands",
            help="Route your own outgoing command-prefixed messages.",
        ),
        command_prefix: str | None = typer.Option(
            None,
            "--command-prefix",
            help="Command prefix used when process_self_commands or group command mode is active.",
        ),
        ingress_batch_enabled: bool | None = typer.Option(
            None,
            "--ingress-batch-enabled/--no-ingress-batch-enabled",
            help="Delay and coalesce sequential inbound messages before one turn.",
        ),
        ingress_debounce_ms: int | None = typer.Option(
            None,
            "--ingress-debounce-ms",
            help="Quiet-window delay before flushing one coalesced inbound batch.",
        ),
        ingress_cooldown_sec: int | None = typer.Option(
            None,
            "--ingress-cooldown-sec",
            help="Minimum seconds between processed inbound batches per chat when ingress batching is enabled.",
        ),
        ingress_max_batch_size: int | None = typer.Option(
            None,
            "--ingress-max-batch-size",
            help="Maximum inbound messages merged into one turn before immediate flush.",
        ),
        ingress_max_buffer_chars: int | None = typer.Option(
            None,
            "--ingress-max-buffer-chars",
            help="Maximum buffered text chars per coalesced inbound batch.",
        ),
        humanize_replies: bool | None = typer.Option(
            None,
            "--humanize-replies/--no-humanize-replies",
            help="Show read receipts/typing and delay replies for a more human-like cadence.",
        ),
        humanize_min_delay_ms: int | None = typer.Option(
            None,
            "--humanize-min-delay-ms",
            help="Minimum reply delay when humanized replies are enabled.",
        ),
        humanize_max_delay_ms: int | None = typer.Option(
            None,
            "--humanize-max-delay-ms",
            help="Maximum reply delay when humanized replies are enabled.",
        ),
        humanize_chars_per_second: int | None = typer.Option(
            None,
            "--humanize-chars-per-second",
            help="Approximate typing speed used to scale reply delay from message length.",
        ),
        mark_read_before_reply: bool | None = typer.Option(
            None,
            "--mark-read-before-reply/--no-mark-read-before-reply",
            help="Send a read receipt before replying when the channel answers from the user account.",
        ),
        watcher_enabled: bool | None = typer.Option(
            None,
            "--watcher-enabled/--no-watcher-enabled",
            help="Collect watched dialog activity into periodic digest turns.",
        ),
        watcher_unmuted_only: bool | None = typer.Option(
            None,
            "--watcher-unmuted-only/--watcher-all-dialogs",
            help="Track only dialogs whose notifications are effectively unmuted.",
        ),
        watcher_include_private: bool | None = typer.Option(
            None,
            "--watcher-include-private/--watcher-no-private",
            help="Include direct chats in watcher digests.",
        ),
        watcher_include_groups: bool | None = typer.Option(
            None,
            "--watcher-include-groups/--watcher-no-groups",
            help="Include groups and supergroups in watcher digests.",
        ),
        watcher_include_channels: bool | None = typer.Option(
            None,
            "--watcher-include-channels/--watcher-no-channels",
            help="Include channel posts in watcher digests.",
        ),
        watcher_batch_interval_sec: int | None = typer.Option(
            None,
            "--watcher-batch-interval-sec",
            help="How often to flush collected watcher events into one digest turn.",
        ),
        watcher_dialog_refresh_interval_sec: int | None = typer.Option(
            None,
            "--watcher-dialog-refresh-interval-sec",
            help="How often to refresh watched dialog metadata and notification settings.",
        ),
        watcher_max_batch_size: int | None = typer.Option(
            None,
            "--watcher-max-batch-size",
            help="Maximum watched events included in one digest turn.",
        ),
        watcher_max_buffer_size: int | None = typer.Option(
            None,
            "--watcher-max-buffer-size",
            help="Maximum in-memory watcher backlog before oldest events are dropped.",
        ),
        watcher_max_message_chars: int | None = typer.Option(
            None,
            "--watcher-max-message-chars",
            help="Per-message clip length for watcher batches.",
        ),
        watcher_blocked_chat_patterns: str | None = typer.Option(
            None,
            "--watcher-blocked-chat-patterns",
            help="Comma-separated case-insensitive contact name, username, or chat title substrings to exclude.",
        ),
        watcher_allowed_chat_patterns: str | None = typer.Option(
            None,
            "--watcher-allowed-chat-patterns",
            help="Comma-separated case-insensitive contact name, username, or chat title substrings allowlist.",
        ),
        watcher_delivery_transport: str | None = typer.Option(
            None,
            "--watcher-delivery-transport",
            help="Optional explicit transport for watcher digests. Defaults to telegram_user Saved Messages.",
        ),
        watcher_delivery_account_id: str | None = typer.Option(
            None,
            "--watcher-delivery-account-id",
            help="Optional explicit account_id for watcher delivery target.",
        ),
        watcher_delivery_peer_id: str | None = typer.Option(
            None,
            "--watcher-delivery-peer-id",
            help="Optional explicit peer_id for watcher delivery target.",
        ),
        watcher_delivery_credential_profile_key: str | None = typer.Option(
            None,
            "--watcher-delivery-credential-profile",
            help="Credential profile for watcher digest delivery when the target transport is not telegram_user.",
        ),
        create_binding: bool | None = typer.Option(
            None,
            "--binding/--no-binding",
            help="Create/update matching routing binding; --no-binding keeps any existing one.",
        ),
        session_policy: SessionPolicy | None = typer.Option(
            None,
            "--session-policy",
            help="Binding session policy when --binding is enabled.",
        ),
        prompt_overlay: str | None = typer.Option(
            None,
            "--prompt-overlay",
            help="Optional routing prompt overlay applied through the matching binding.",
        ),
        priority: int = typer.Option(0, "--priority", help="Binding priority when --binding is enabled."),
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Use defaults plus explicit flags without interactive questions.",
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
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Create one Telethon endpoint, optionally with a matching routing binding."""

        try:
            result = create_telethon_channel(
                channel_id=channel_id,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
                account_id=account_id,
                enabled=enabled,
                reply_mode=reply_mode,
                tool_profile=tool_profile,
                reply_blocked_chat_patterns=reply_blocked_chat_patterns,
                reply_allowed_chat_patterns=reply_allowed_chat_patterns,
                group_invocation_mode=group_invocation_mode,
                process_self_commands=process_self_commands,
                command_prefix=command_prefix,
                ingress_batch_enabled=ingress_batch_enabled,
                ingress_debounce_ms=ingress_debounce_ms,
                ingress_cooldown_sec=ingress_cooldown_sec,
                ingress_max_batch_size=ingress_max_batch_size,
                ingress_max_buffer_chars=ingress_max_buffer_chars,
                humanize_replies=humanize_replies,
                humanize_min_delay_ms=humanize_min_delay_ms,
                humanize_max_delay_ms=humanize_max_delay_ms,
                humanize_chars_per_second=humanize_chars_per_second,
                mark_read_before_reply=mark_read_before_reply,
                watcher_enabled=watcher_enabled,
                watcher_unmuted_only=watcher_unmuted_only,
                watcher_include_private=watcher_include_private,
                watcher_include_groups=watcher_include_groups,
                watcher_include_channels=watcher_include_channels,
                watcher_batch_interval_sec=watcher_batch_interval_sec,
                watcher_dialog_refresh_interval_sec=watcher_dialog_refresh_interval_sec,
                watcher_max_batch_size=watcher_max_batch_size,
                watcher_max_buffer_size=watcher_max_buffer_size,
                watcher_max_message_chars=watcher_max_message_chars,
                watcher_blocked_chat_patterns=watcher_blocked_chat_patterns,
                watcher_allowed_chat_patterns=watcher_allowed_chat_patterns,
                watcher_delivery_transport=watcher_delivery_transport,
                watcher_delivery_account_id=watcher_delivery_account_id,
                watcher_delivery_peer_id=watcher_delivery_peer_id,
                watcher_delivery_credential_profile_key=watcher_delivery_credential_profile_key,
                create_binding=create_binding,
                session_policy=session_policy,
                prompt_overlay=prompt_overlay,
                priority=priority,
                yes=yes,
                lang=lang,
                ru=ru,
            )
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        settings = get_settings()
        if json_output:
            payload: dict[str, object] = {"channel": result.saved.model_dump(mode="json")}
            if result.binding_warning:
                payload["warning"] = result.binding_warning
            if result.policy_warning:
                payload["policy_warning"] = result.policy_warning
            typer.echo(json.dumps(payload, ensure_ascii=True))
            reload_legacy_managed_runtime_notice(settings)
            return
        typer.echo(
            f"Telethon channel `{result.saved.endpoint_id}` saved for profile `{result.saved.profile_id}` "
            f"(credential_profile={result.saved.credential_profile_key}, account_id={result.saved.account_id}, "
            f"reply_mode={result.saved.reply_mode}, tool_profile={result.saved.tool_profile}, "
            f"group_invocation_mode={result.saved.group_invocation_mode}, "
            f"process_self_commands={result.saved.process_self_commands}, ingress_batch={result.saved.ingress_batch.enabled}, "
            f"humanize_replies={result.saved.reply_humanization.enabled}, "
            f"watcher_enabled={result.saved.watcher.enabled}, enabled={result.saved.enabled})."
        )
        if result.binding_created:
            typer.echo(f"Matching binding `{result.saved.endpoint_id}` was also created/updated.")
        elif result.binding_warning:
            typer.secho(result.binding_warning, fg=typer.colors.YELLOW, err=True)
        if result.policy_warning:
            typer.secho(result.policy_warning, fg=typer.colors.YELLOW, err=True)
        reload_legacy_managed_runtime_notice(settings)


__all__ = ["register_telethon_add_command"]
