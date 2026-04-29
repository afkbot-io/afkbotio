"""Interactive chat command backed by AgentLoop runtime helpers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from pathlib import Path
from typing import cast

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
from afkbot.services.browser_sessions import BrowserSessionManager, get_browser_session_manager
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.runtime_factory import resolve_profile_settings
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.agent_loop.turn_runtime import (
    open_serialized_turn_runner,
    run_once_result,
    submit_secure_field,
)
from afkbot.services.session_orchestration import SerializedSessionTurnRunner
from afkbot.services.policy import infer_workspace_scope_mode
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.profile_runtime.service import ProfileServiceError, run_profile_service_sync
from afkbot.services.agent_loop.sessions import (
    SessionProfileMismatchError,
    ensure_session_exists,
)
from afkbot.services.chat_session.terminal_lock import (
    ChatSessionTerminalLockedError,
    get_chat_session_terminal_lock,
)
from afkbot.services.chat_session.turn_flow import SerializedTurnRunnerFactory
from afkbot.services.tools.base import ToolCall
from afkbot.services.llm_timeout_policy import (
    DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
    DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC,
)
from afkbot.settings import Settings, get_settings

_DEFAULT_LLM_EXECUTION_BUDGET_LOW_SEC = 900.0
_DEFAULT_LLM_EXECUTION_BUDGET_MEDIUM_SEC = 1800.0
_DEFAULT_LLM_EXECUTION_BUDGET_HIGH_SEC = 3600.0

_CHAT_LLM_REQUEST_TIMEOUT_SEC = 120.0
_CHAT_LLM_EXECUTION_BUDGET_LOW_SEC = 120.0
_CHAT_LLM_EXECUTION_BUDGET_MEDIUM_SEC = 180.0
_CHAT_LLM_EXECUTION_BUDGET_HIGH_SEC = 300.0
_CHAT_LLM_EXECUTION_BUDGET_VERY_HIGH_SEC = 600.0


def register(app: typer.Typer) -> None:
    """Register chat command in Typer app."""

    @app.command("chat")
    def chat(
        session_name: str | None = typer.Argument(
            None,
            help="Optional chat session name. Reuses the same named session; if omitted, AFKBOT creates a fresh session id for this chat invocation.",
        ),
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
            help="Exact chat session id used for history, runs, and secure resume state. Prefer the optional positional session name for reusable named chats.",
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
            session_name=session_name,
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
        session_terminal_lock = get_chat_session_terminal_lock(root_dir=chat_settings.root_dir)
        session_terminal_guard = (
            session_terminal_lock.acquire(
                profile_id=target.profile_id,
                session_id=target.session_id,
            )
            if target.terminal_lock_required
            else nullcontext()
        )
        try:
            with session_terminal_guard:
                try:
                    asyncio.run(
                        ensure_session_exists(
                            settings=chat_settings,
                            profile_id=target.profile_id,
                            session_id=target.session_id,
                            title=target.session_label or target.session_id,
                        )
                    )
                except SessionProfileMismatchError as exc:
                    raise_usage_error(str(exc))
                effective_profile_settings = resolve_profile_settings(
                    settings=chat_settings,
                    profile_id=target.profile_id,
                    ensure_layout=True,
                )
                resolved_plan_mode = (
                    normalize_chat_planning_mode(plan or effective_profile_settings.chat_planning_mode)
                    or "off"
                )
                resolved_thinking_level = resolve_cli_thinking_level(
                    explicit_value=thinking_level,
                    default_value=effective_profile_settings.llm_thinking_level,
                )
                runtime_overrides = build_cli_runtime_overrides(
                    target=target.runtime_target,
                    transport=transport,
                    account_id=account_id,
                    peer_id=peer_id,
                    thread_id=thread_id,
                    user_id=user_id,
                )

                async def _run_once_result_with_chat_settings(
                    *,
                    message: str,
                    profile_id: str,
                    session_id: str,
                    planned_tool_calls: list[ToolCall] | None = None,
                    progress_sink: Callable[[ProgressEvent], None] | None = None,
                    context_overrides: TurnContextOverrides | None = None,
                ) -> TurnResult:
                    return await run_once_result(
                        message=message,
                        profile_id=profile_id,
                        session_id=session_id,
                        settings=chat_settings,
                        planned_tool_calls=planned_tool_calls,
                        progress_sink=progress_sink,
                        context_overrides=context_overrides,
                    )

                run_turn_with_secure_resolution: RunTurnWithSecureResolution = (
                    build_run_turn_with_overrides(
                        runtime_overrides,
                        run_once_result_fn=_run_once_result_with_chat_settings,
                        submit_secure_field_fn=submit_secure_field,
                        confirm_space_fn=None if message is not None else confirm_space,
                    )
                )

                def _serialized_turn_runner_factory(
                    profile_id: str,
                    session_id: str,
                ) -> AbstractAsyncContextManager[SerializedSessionTurnRunner]:
                    return open_serialized_turn_runner(
                        profile_id=profile_id,
                        session_id=session_id,
                        settings=chat_settings,
                    )

                if (
                    not json_output
                    and supports_interactive_tty()
                    and not handle_chat_update_notice(settings=settings)
                ):
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
                        serialized_turn_runner_factory=_serialized_turn_runner_factory,
                    )
                    return
                if json_output:
                    raise_usage_error("--json is only supported with --message")
                _invoke_run_repl(
                    profile_id=target.profile_id,
                    session_id=target.session_id,
                    session_label=target.session_label,
                    run_turn_with_secure_resolution=run_turn_with_secure_resolution,
                    get_browser_session_manager=get_browser_session_manager,
                    get_settings=lambda: chat_settings,
                    planning_mode=resolved_plan_mode,
                    thinking_level=resolved_thinking_level,
                    serialized_turn_runner_factory=_serialized_turn_runner_factory,
                )
        except ChatSessionTerminalLockedError as exc:
            raise_usage_error(exc.reason)
        except RuntimeError as exc:
            if str(exc) == "Terminal session lock is unavailable on this platform.":
                raise_usage_error(
                    "Interactive terminal chat session locking is unavailable on this platform. "
                    "Use --message for one-shot chat or run on a platform with fcntl support."
                )
            raise


def _resolve_chat_invocation_settings(
    *,
    settings: Settings,
    profile_id: str,
    invocation_cwd: Path,
) -> Settings:
    """Return one invocation-scoped settings object for chat cwd behavior."""

    updates: dict[str, object] = _chat_default_llm_budget_updates(settings)
    if settings.tool_workspace_root is None and _profile_has_full_chat_workspace_access(
        settings=settings, profile_id=profile_id
    ):
        updates["tool_invocation_cwd"] = invocation_cwd.resolve(strict=False)
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _invoke_run_repl(
    *,
    profile_id: str,
    session_id: str,
    session_label: str | None,
    run_turn_with_secure_resolution: RunTurnWithSecureResolution,
    get_browser_session_manager: Callable[[], BrowserSessionManager],
    get_settings: Callable[[], Settings],
    planning_mode: str,
    thinking_level: str | None,
    serialized_turn_runner_factory: SerializedTurnRunnerFactory | None,
) -> None:
    """Call `run_repl` with only the kwargs supported by the installed runtime."""

    kwargs: dict[str, object] = {
        "profile_id": profile_id,
        "session_id": session_id,
        "session_label": session_label,
        "run_turn_with_secure_resolution": run_turn_with_secure_resolution,
        "get_browser_session_manager": get_browser_session_manager,
        "get_settings": get_settings,
        "planning_mode": planning_mode,
        "thinking_level": thinking_level,
        "serialized_turn_runner_factory": serialized_turn_runner_factory,
    }
    signature = inspect.signature(run_repl)
    invoke = cast(Callable[..., None], run_repl)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        invoke(**kwargs)
        return
    filtered_kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    invoke(**filtered_kwargs)

def _chat_default_llm_budget_updates(settings: Settings) -> dict[str, object]:
    """Cap inherited long-running defaults for foreground chat turns."""

    explicit_fields: set[str] = getattr(settings, "model_fields_set", set())
    budget_specs = (
        (
            "llm_request_timeout_sec",
            DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
            _CHAT_LLM_REQUEST_TIMEOUT_SEC,
        ),
        (
            "llm_execution_budget_low_sec",
            _DEFAULT_LLM_EXECUTION_BUDGET_LOW_SEC,
            _CHAT_LLM_EXECUTION_BUDGET_LOW_SEC,
        ),
        (
            "llm_execution_budget_medium_sec",
            _DEFAULT_LLM_EXECUTION_BUDGET_MEDIUM_SEC,
            _CHAT_LLM_EXECUTION_BUDGET_MEDIUM_SEC,
        ),
        (
            "llm_execution_budget_high_sec",
            _DEFAULT_LLM_EXECUTION_BUDGET_HIGH_SEC,
            _CHAT_LLM_EXECUTION_BUDGET_HIGH_SEC,
        ),
        (
            "llm_execution_budget_very_high_sec",
            DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC,
            _CHAT_LLM_EXECUTION_BUDGET_VERY_HIGH_SEC,
        ),
    )
    return {
        field_name: chat_limit
        for field_name, default_value, chat_limit in budget_specs
        if field_name not in explicit_fields
        and float(getattr(settings, field_name)) >= default_value
    }


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
