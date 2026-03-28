"""Interactive chat command backed by AgentLoop runtime helpers."""

from __future__ import annotations

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.chat_planning import (
    normalize_chat_planning_mode,
    resolve_cli_thinking_level,
)
from afkbot.cli.presentation import confirm_space
from afkbot.cli.commands.chat_secure_flow import (
    RunTurnWithSecureResolution,
    build_run_turn_with_overrides,
)
from afkbot.cli.commands.chat_target import build_cli_runtime_overrides, resolve_cli_chat_target
from afkbot.cli.commands.chat_session_runtime import run_repl, run_single_turn
from afkbot.services.browser_sessions import get_browser_session_manager
from afkbot.services.agent_loop.runtime_factory import resolve_profile_settings
from afkbot.services.agent_loop.turn_runtime import run_once_result, submit_secure_field
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register chat command in Typer app."""

    @app.command("chat")
    def chat(
        message: str | None = typer.Option(
            None,
            "--message",
            help="One-turn user message. If omitted, starts interactive REPL.",
        ),
        profile: str = typer.Option(
            "default",
            "--profile",
            help="Runtime profile id for the turn or REPL session.",
        ),
        session: str | None = typer.Option(
            None,
            "--session",
            help="Chat session id used for history, runs, and secure resume state. Defaults to one profile-scoped CLI session.",
        ),
        resolve_binding: bool = typer.Option(
            False,
            "--resolve-binding/--no-resolve-binding",
            help="Resolve effective profile/session via persisted channel binding rules.",
        ),
        require_binding_match: bool = typer.Option(
            False,
            "--require-binding-match/--allow-binding-fallback",
            help="Fail when binding mode is enabled but no persisted rule matches the provided selectors.",
        ),
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Normalized transport name used for binding resolution.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Optional transport account/bot id for binding resolution.",
        ),
        peer_id: str | None = typer.Option(
            None,
            "--peer-id",
            help="Optional chat/group/peer id for binding resolution.",
        ),
        thread_id: str | None = typer.Option(
            None,
            "--thread-id",
            help="Optional thread/topic id for binding resolution.",
        ),
        user_id: str | None = typer.Option(
            None,
            "--user-id",
            help="Optional user id for binding resolution.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print raw JSON turn payload instead of assistant-formatted output.",
        ),
        plan: str | None = typer.Option(
            None,
            "--plan",
            help="Plan-first mode: off, auto, or on.",
        ),
        thinking_level: str | None = typer.Option(
            None,
            "--thinking-level",
            help="Reasoning budget: low, medium, high, or very_high.",
        ),
    ) -> None:
        """Run one chat turn or open the interactive REPL terminal chat session."""

        settings = get_settings()
        target = resolve_cli_chat_target(
            settings=settings,
            profile_id=profile,
            session_id=session,
            resolve_binding=resolve_binding,
            require_binding_match=require_binding_match,
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        )
        effective_profile_settings = resolve_profile_settings(
            settings=settings,
            profile_id=target.profile_id,
            ensure_layout=True,
        )
        resolved_plan_mode = normalize_chat_planning_mode(
            plan or effective_profile_settings.chat_planning_mode
        ) or "off"
        resolved_thinking_level = resolve_cli_thinking_level(
            explicit_value=thinking_level,
            default_value=effective_profile_settings.llm_thinking_level,
        )
        runtime_overrides = build_cli_runtime_overrides(
            target=target,
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        )
        run_turn_with_secure_resolution: RunTurnWithSecureResolution = build_run_turn_with_overrides(
            runtime_overrides,
            run_once_result_fn=run_once_result,
            submit_secure_field_fn=submit_secure_field,
            confirm_space_fn=confirm_space,
        )

        if message is not None:
            run_single_turn(
                message=message,
                profile_id=target.profile_id,
                session_id=target.session_id,
                json_output=json_output,
                run_turn_with_secure_resolution=run_turn_with_secure_resolution,
                planning_mode=resolved_plan_mode,
                thinking_level=resolved_thinking_level,
            )
            return
        if json_output:
            raise_usage_error("--json is only supported with --message")
        run_repl(
            profile_id=target.profile_id,
            session_id=target.session_id,
            run_turn_with_secure_resolution=run_turn_with_secure_resolution,
            get_browser_session_manager=get_browser_session_manager,
            get_settings=get_settings,
            planning_mode=resolved_plan_mode,
            thinking_level=resolved_thinking_level,
        )
