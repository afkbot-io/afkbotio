"""Interactive chat command backed by AgentLoop runtime helpers."""

from __future__ import annotations

import inspect
from pathlib import Path

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.chat_planning import (
    normalize_chat_planning_mode,
    resolve_cli_thinking_level,
)
from afkbot.cli.commands.chat_update_notices import handle_chat_update_notice
from afkbot.cli.presentation import confirm_space
from afkbot.cli.presentation.tty import supports_interactive_tty
from afkbot.cli.commands.chat_secure_flow import (
    RunTurnWithSecureResolution,
    build_run_turn_with_overrides,
)
from afkbot.cli.commands.chat_target import build_cli_runtime_overrides, resolve_cli_chat_target
from afkbot.cli.commands.chat_session_runtime import run_repl, run_single_turn
from afkbot.services.browser_sessions import get_browser_session_manager
from afkbot.services.agent_loop.runtime_factory import resolve_profile_settings
from afkbot.services.agent_loop.turn_runtime import run_once_result, submit_secure_field
from afkbot.services.policy import infer_workspace_scope_mode
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.profile_runtime.service import ProfileServiceError, run_profile_service_sync
from afkbot.settings import Settings, get_settings


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
        chat_settings = _resolve_chat_invocation_settings(
            settings=settings,
            profile_id=target.profile_id,
            invocation_cwd=Path.cwd(),
        )
        effective_profile_settings = resolve_profile_settings(
            settings=chat_settings,
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
        run_once_result_accepts_settings = "settings" in inspect.signature(run_once_result).parameters

        async def _run_once_result_with_chat_settings(**kwargs: object):
            if run_once_result_accepts_settings:
                return await run_once_result(settings=chat_settings, **kwargs)
            return await run_once_result(**kwargs)

        run_turn_with_secure_resolution: RunTurnWithSecureResolution = build_run_turn_with_overrides(
            runtime_overrides,
            run_once_result_fn=_run_once_result_with_chat_settings,
            submit_secure_field_fn=submit_secure_field,
            confirm_space_fn=None if message is not None else confirm_space,
        )
        if not json_output and supports_interactive_tty() and not handle_chat_update_notice(settings=settings):
            return

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
            get_settings=lambda: chat_settings,
            planning_mode=resolved_plan_mode,
            thinking_level=resolved_thinking_level,
        )


def _resolve_chat_invocation_settings(
    *,
    settings: Settings,
    profile_id: str,
    invocation_cwd: Path,
) -> Settings:
    """Return one invocation-scoped settings object for chat cwd behavior."""

    if settings.tool_workspace_root is not None:
        return settings
    if not _profile_has_full_chat_workspace_access(settings=settings, profile_id=profile_id):
        return settings
    return settings.model_copy(
        update={"tool_invocation_cwd": invocation_cwd.resolve(strict=False)}
    )


def _profile_has_full_chat_workspace_access(*, settings: Settings, profile_id: str) -> bool:
    """Return whether chat should start from the operator's current cwd."""

    try:
        profile = run_profile_service_sync(
            settings,
            lambda service: service.get(profile_id=profile_id),
        )
    except ProfileServiceError:
        return False

    if not profile.policy.enabled:
        return True
    if not profile.policy.allowed_directories:
        return False
    scope_mode = infer_workspace_scope_mode(
        root_dir=settings.root_dir,
        profile_root=get_profile_runtime_config_service(settings).profile_root(profile_id),
        allowed_directories=profile.policy.allowed_directories,
    )
    return scope_mode == "full_system"
