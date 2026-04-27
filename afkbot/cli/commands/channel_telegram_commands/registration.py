"""Typer command registration for Telegram channel CLI controls."""

from __future__ import annotations

from collections.abc import Callable

import typer

from afkbot.cli.commands.channel_telegram_commands.mutations import (
    run_telegram_add,
    run_telegram_update,
)
from afkbot.cli.commands.channel_telegram_commands.operations import (
    run_telegram_delete,
    run_telegram_list,
    run_telegram_poll_once,
    run_telegram_reset_offset,
    run_telegram_show,
    run_telegram_status,
)
from afkbot.cli.commands.channel_telegram_commands.runtime import TelegramCommandRuntime
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channels.tool_profiles import CHANNEL_TOOL_PROFILE_HELP

type TelegramRuntimeFactory = Callable[[], TelegramCommandRuntime]


def register_telegram_command_tree(
    channel_app: typer.Typer,
    *,
    runtime_factory: TelegramRuntimeFactory,
) -> None:
    """Register Telegram Bot API polling commands under `afk channel telegram`."""

    telegram_app = typer.Typer(
        help="Telegram Bot API polling controls.",
        no_args_is_help=True,
    )
    channel_app.add_typer(telegram_app, name="telegram")

    @telegram_app.command("add")
    def telegram_add(
        channel_id: str | None = typer.Argument(
            None,
            help="Stable channel id used later in show/update/status. Omit it to let the wizard suggest an auto-generated id.",
        ),
        profile_id: str | None = typer.Option(None, "--profile", help="Target profile-agent id."),
        credential_profile_key: str | None = typer.Option(
            None,
            "--credential-profile",
            help="Telegram credential profile key holding the bot token. Defaults to the channel id.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Logical account id used by bindings and routing telemetry. Defaults to the channel id.",
        ),
        enabled: bool | None = typer.Option(None, "--enabled/--disabled", help="Enable or disable this endpoint."),
        group_trigger_mode: str | None = typer.Option(
            None,
            "--group-trigger-mode",
            help="Group/supergroup trigger mode: mention_or_reply, reply_only, mention_only, all_messages.",
        ),
        private_policy: str | None = typer.Option(
            None,
            "--private-policy",
            help="Private chat access: open, allowlist, disabled.",
            case_sensitive=False,
        ),
        allow_from: str | None = typer.Option(
            None,
            "--allow-from",
            help="Comma-separated Telegram user ids allowed in private allowlist mode.",
        ),
        group_policy: str | None = typer.Option(
            None,
            "--group-policy",
            help="Group access: open, allowlist, disabled.",
            case_sensitive=False,
        ),
        groups: str | None = typer.Option(
            None,
            "--groups",
            help="Comma-separated Telegram group/supergroup ids allowed in group allowlist mode.",
        ),
        group_allow_from: str | None = typer.Option(
            None,
            "--group-allow-from",
            help="Comma-separated Telegram user ids allowed to trigger the bot in allowed groups.",
        ),
        outbound_allow_to: str | None = typer.Option(
            None,
            "--outbound-allow-to",
            help="Comma-separated chat ids this endpoint may send outbound messages to.",
        ),
        tool_profile: str | None = typer.Option(
            None,
            "--tool-profile",
            help=CHANNEL_TOOL_PROFILE_HELP,
            case_sensitive=False,
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
            help="Emit Telegram typing actions and delay replies for a more human-like cadence.",
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
            help="Use defaults plus explicit flags without interactive questions. Missing channel id is auto-generated.",
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
        """Create one Telegram polling endpoint, optionally with a matching binding."""

        run_telegram_add(
            runtime=runtime_factory(),
            channel_id=channel_id,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            account_id=account_id,
            enabled=enabled,
            group_trigger_mode=group_trigger_mode,
            private_policy=private_policy,
            allow_from=allow_from,
            group_policy=group_policy,
            groups=groups,
            group_allow_from=group_allow_from,
            outbound_allow_to=outbound_allow_to,
            tool_profile=tool_profile,
            ingress_batch_enabled=ingress_batch_enabled,
            ingress_debounce_ms=ingress_debounce_ms,
            ingress_cooldown_sec=ingress_cooldown_sec,
            ingress_max_batch_size=ingress_max_batch_size,
            ingress_max_buffer_chars=ingress_max_buffer_chars,
            humanize_replies=humanize_replies,
            humanize_min_delay_ms=humanize_min_delay_ms,
            humanize_max_delay_ms=humanize_max_delay_ms,
            humanize_chars_per_second=humanize_chars_per_second,
            create_binding=create_binding,
            session_policy=session_policy,
            prompt_overlay=prompt_overlay,
            priority=priority,
            yes=yes,
            lang=lang,
            ru=ru,
            json_output=json_output,
        )

    @telegram_app.command("update")
    def telegram_update(
        channel_id: str = typer.Argument(..., help="Stable Telegram channel endpoint id."),
        profile_id: str | None = typer.Option(None, "--profile", help="Target profile-agent id."),
        credential_profile_key: str | None = typer.Option(
            None,
            "--credential-profile",
            help="Telegram credential profile key holding the bot token.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Channel account id used by bindings and routing telemetry.",
        ),
        group_trigger_mode: str | None = typer.Option(
            None,
            "--group-trigger-mode",
            help="Group/supergroup trigger mode: mention_or_reply, reply_only, mention_only, all_messages.",
        ),
        private_policy: str | None = typer.Option(
            None,
            "--private-policy",
            help="Private chat access: open, allowlist, disabled.",
            case_sensitive=False,
        ),
        allow_from: str | None = typer.Option(
            None,
            "--allow-from",
            help="Comma-separated Telegram user ids allowed in private allowlist mode.",
        ),
        group_policy: str | None = typer.Option(
            None,
            "--group-policy",
            help="Group access: open, allowlist, disabled.",
            case_sensitive=False,
        ),
        groups: str | None = typer.Option(
            None,
            "--groups",
            help="Comma-separated Telegram group/supergroup ids allowed in group allowlist mode.",
        ),
        group_allow_from: str | None = typer.Option(
            None,
            "--group-allow-from",
            help="Comma-separated Telegram user ids allowed to trigger the bot in allowed groups.",
        ),
        outbound_allow_to: str | None = typer.Option(
            None,
            "--outbound-allow-to",
            help="Comma-separated chat ids this endpoint may send outbound messages to.",
        ),
        tool_profile: str | None = typer.Option(
            None,
            "--tool-profile",
            help=CHANNEL_TOOL_PROFILE_HELP,
            case_sensitive=False,
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
            help="Emit Telegram typing actions and delay replies for a more human-like cadence.",
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
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Use current values plus explicit flags without interactive questions.",
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
        sync_binding: bool = typer.Option(
            False,
            "--binding",
            help="Also update the matching routing binding to the current profile/account values.",
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
        priority: int | None = typer.Option(None, "--priority", help="Binding priority when --binding is enabled."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Update one Telegram polling endpoint without implicit upsert."""

        run_telegram_update(
            runtime=runtime_factory(),
            channel_id=channel_id,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            account_id=account_id,
            group_trigger_mode=group_trigger_mode,
            private_policy=private_policy,
            allow_from=allow_from,
            group_policy=group_policy,
            groups=groups,
            group_allow_from=group_allow_from,
            outbound_allow_to=outbound_allow_to,
            tool_profile=tool_profile,
            ingress_batch_enabled=ingress_batch_enabled,
            ingress_debounce_ms=ingress_debounce_ms,
            ingress_cooldown_sec=ingress_cooldown_sec,
            ingress_max_batch_size=ingress_max_batch_size,
            ingress_max_buffer_chars=ingress_max_buffer_chars,
            humanize_replies=humanize_replies,
            humanize_min_delay_ms=humanize_min_delay_ms,
            humanize_max_delay_ms=humanize_max_delay_ms,
            humanize_chars_per_second=humanize_chars_per_second,
            yes=yes,
            lang=lang,
            ru=ru,
            sync_binding=sync_binding,
            session_policy=session_policy,
            prompt_overlay=prompt_overlay,
            priority=priority,
            json_output=json_output,
        )

    @telegram_app.command("list")
    def telegram_list(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """List configured Telegram polling endpoints."""

        run_telegram_list(runtime=runtime_factory(), json_output=json_output)

    @telegram_app.command("show")
    def telegram_show(
        channel_id: str = typer.Argument(..., help="Telegram channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show one Telegram polling endpoint."""

        run_telegram_show(
            runtime=runtime_factory(),
            channel_id=channel_id,
            json_output=json_output,
        )

    @telegram_app.command("enable")
    def telegram_enable(
        channel_id: str = typer.Argument(..., help="Telegram channel endpoint id."),
    ) -> None:
        """Enable one Telegram endpoint."""

        runtime_factory().set_endpoint_enabled(channel_id, True)

    @telegram_app.command("disable")
    def telegram_disable(
        channel_id: str = typer.Argument(..., help="Telegram channel endpoint id."),
    ) -> None:
        """Disable one Telegram endpoint."""

        runtime_factory().set_endpoint_enabled(channel_id, False)

    @telegram_app.command("delete")
    def telegram_delete(
        channel_id: str = typer.Argument(..., help="Telegram channel endpoint id."),
        keep_binding: bool = typer.Option(
            False,
            "--keep-binding",
            help="Keep routing binding with the same id instead of deleting it too.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Delete one Telegram polling endpoint and its saved offset state."""

        run_telegram_delete(
            runtime=runtime_factory(),
            channel_id=channel_id,
            keep_binding=keep_binding,
            json_output=json_output,
        )

    @telegram_app.command("status")
    def telegram_status(
        channel_id: str | None = typer.Argument(
            None,
            help="Optional Telegram channel endpoint id. When omitted, show all endpoints.",
        ),
        probe: bool = typer.Option(False, "--probe", help="Run live Telegram getMe probe."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show Telegram polling endpoint status."""

        run_telegram_status(
            runtime=runtime_factory(),
            channel_id=channel_id,
            probe=probe,
            json_output=json_output,
        )

    @telegram_app.command("poll-once")
    def telegram_poll_once(
        channel_id: str = typer.Argument(..., help="Telegram channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Run one polling iteration and process one Telegram update batch."""

        run_telegram_poll_once(
            runtime=runtime_factory(),
            channel_id=channel_id,
            json_output=json_output,
        )

    @telegram_app.command("reset-offset")
    def telegram_reset_offset(
        channel_id: str = typer.Argument(..., help="Telegram channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Reset saved Telegram polling offset state."""

        run_telegram_reset_offset(
            runtime=runtime_factory(),
            channel_id=channel_id,
            json_output=json_output,
        )


__all__ = ["TelegramRuntimeFactory", "register_telegram_command_tree"]
