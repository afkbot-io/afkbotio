"""Session-level runtime helpers for interactive and one-shot chat CLI flows."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import typer

from afkbot.cli.commands.chat_planning_runtime import (
    build_repl_planning_callbacks,
    confirm_chat_plan_first,
    render_captured_plan,
)
from afkbot.cli.commands.chat_planning import ChatPlanningMode
from afkbot.cli.commands.chat_repl_runtime import run_repl_transport
from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.presentation.chat_interactive import InteractiveChatUX
from afkbot.cli.presentation.chat_style import AFK_AGENT_HEADER
from afkbot.cli.presentation.chat_turn_output import render_chat_turn_outcome
from afkbot.cli.presentation.progress_timeline import (
    ProgressTimelineState,
    reduce_progress_event,
)
from afkbot.cli.presentation.tty import supports_interactive_tty
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.browser_sessions import BrowserSessionManager
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.chat_session.session_state import ChatPlanPhase, ChatReplSessionState
from afkbot.services.chat_session.turn_flow import (
    ChatTurnInteractiveOptions,
    ChatTurnOutcome,
    RunTurnWithSecureResolution,
    run_chat_turn_with_optional_planning,
)
from afkbot.services.llm.reasoning import ThinkingLevel
from afkbot.services.chat_session.turn_flow import SerializedTurnRunnerFactory
from afkbot.settings import Settings


def run_single_turn(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    json_output: bool,
    run_turn_with_secure_resolution: RunTurnWithSecureResolution,
    planning_mode: ChatPlanningMode,
    thinking_level: ThinkingLevel | None,
    serialized_turn_runner_factory: SerializedTurnRunnerFactory | None = None,
) -> None:
    """Execute one non-REPL chat turn with progress rendering."""

    if not message.strip():
        raise_usage_error("--message cannot be empty; omit --message to start interactive chat")

    timeline_state = ProgressTimelineState()
    progress_opened = False
    interactive_ux = (
        None if json_output or not _supports_interactive_confirm() else InteractiveChatUX.create()
    )

    def echo_progress(event: ProgressEvent) -> None:
        nonlocal timeline_state, progress_opened
        if interactive_ux is not None:
            interactive_ux.on_progress(event)
            return
        timeline_state, frame = reduce_progress_event(timeline_state, event)
        if frame is None:
            return
        color = frame.color
        if not progress_opened:
            typer.echo()
            typer.echo(AFK_AGENT_HEADER)
            progress_opened = True
        if frame.separator_before:
            typer.echo("")
        if frame.spinner_label is not None:
            typer.echo(f"  {color}{frame.spinner_label}...\033[0m")
            return
        if frame.status_line is not None:
            typer.echo(f"  {color}{frame.status_line}\033[0m")
        if frame.detail_line is not None:
            typer.echo(f"    \033[90m{frame.detail_line}\033[0m")

    if interactive_ux is not None:
        setattr(echo_progress, "before_interactive_prompt", interactive_ux.stop_progress)

    try:
        if interactive_ux is not None:
            interactive_ux.begin_agent_turn()
        result: ChatTurnOutcome = asyncio.run(
            run_chat_turn_with_optional_planning(
                message=message,
                profile_id=profile_id,
                session_id=session_id,
                progress_sink=None if json_output else echo_progress,
                allow_secure_prompt=not json_output,
                run_turn_with_secure_resolution=run_turn_with_secure_resolution,
                planning_mode=planning_mode,
                thinking_level=thinking_level,
                prompt_to_plan_first=(
                    confirm_chat_plan_first
                    if _supports_interactive_confirm() and not json_output
                    else None
                ),
                confirm_plan_execution=None,
                present_plan=lambda plan_result, plan_snapshot: typer.echo(
                    render_captured_plan(
                        plan_result=plan_result,
                        plan_snapshot=plan_snapshot,
                    )
                ),
                record_plan=None,
                serialized_turn_runner_factory=serialized_turn_runner_factory,
            )
        )
    except asyncio.CancelledError:
        typer.echo("cancelled")
        raise typer.Exit(code=0) from None
    finally:
        if interactive_ux is not None:
            interactive_ux.stop_progress()

    if json_output:
        typer.echo(result.result.model_dump_json())
        return

    rendered_output = render_chat_turn_outcome(
        result,
        include_header=not (progress_opened or interactive_ux is not None),
        leading_blank_line=True,
    )
    if rendered_output is not None:
        typer.echo(rendered_output)


def run_repl(
    *,
    profile_id: str,
    session_id: str,
    run_turn_with_secure_resolution: RunTurnWithSecureResolution,
    get_browser_session_manager: Callable[[], BrowserSessionManager],
    get_settings: Callable[[], Settings],
    planning_mode: ChatPlanningMode,
    thinking_level: ThinkingLevel | None,
    serialized_turn_runner_factory: SerializedTurnRunnerFactory | None = None,
) -> None:
    """Run interactive loop until explicit exit command or EOF."""

    async def _run_turn(
        message: str,
        progress_sink: Callable[[ProgressEvent], None],
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        return await _run_repl_turn(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            progress_sink=progress_sink,
            run_turn_with_secure_resolution=run_turn_with_secure_resolution,
            repl_state=repl_state,
            turn_options=turn_options,
            serialized_turn_runner_factory=serialized_turn_runner_factory,
        )

    run_repl_transport(
        profile_id=profile_id,
        session_id=session_id,
        run_turn=_run_turn,
        get_browser_session_manager=get_browser_session_manager,
        get_settings=get_settings,
        planning_mode=planning_mode,
        thinking_level=thinking_level,
    )


def _store_repl_plan(snapshot: ChatPlanSnapshot, repl_state: ChatReplSessionState) -> None:
    """Persist the latest plan snapshot in local REPL session state."""

    repl_state.latest_plan = snapshot
    repl_state.latest_plan_phase = "planned"


def _update_repl_plan_phase(phase: ChatPlanPhase, repl_state: ChatReplSessionState) -> None:
    """Track the current execution phase for the latest stored plan."""

    if repl_state.latest_plan is None:
        return
    repl_state.latest_plan_phase = phase


async def _run_repl_turn(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    progress_sink: Callable[[ProgressEvent], None],
    run_turn_with_secure_resolution: RunTurnWithSecureResolution,
    repl_state: ChatReplSessionState,
    turn_options: ChatTurnInteractiveOptions,
    serialized_turn_runner_factory: SerializedTurnRunnerFactory | None = None,
) -> ChatTurnOutcome:
    """Run one REPL turn with the current local planning and thinking settings."""

    planning_callbacks = build_repl_planning_callbacks(
        planning_mode=repl_state.planning_mode,
        interactive_confirm=turn_options.interactive_confirm,
        print_intermediate=lambda text: typer.echo(text),
    )
    present_plan = (
        turn_options.present_plan
        if turn_options.present_plan is not None
        else planning_callbacks.present_plan
    )
    return await run_chat_turn_with_optional_planning(
        message=message,
        profile_id=profile_id,
        session_id=session_id,
        progress_sink=progress_sink,
        allow_secure_prompt=True,
        run_turn_with_secure_resolution=run_turn_with_secure_resolution,
        planning_mode=repl_state.planning_mode,
        thinking_level=repl_state.thinking_level,
        prompt_to_plan_first=(
            turn_options.prompt_to_plan_first
            if turn_options.prompt_to_plan_first is not None
            else planning_callbacks.prompt_to_plan_first
        ),
        confirm_plan_execution=(
            turn_options.confirm_plan_execution
            if turn_options.confirm_plan_execution is not None
            else planning_callbacks.confirm_plan_execution
        ),
        present_plan=present_plan,
        record_plan=lambda snapshot: _store_repl_plan(snapshot, repl_state),
        update_plan_phase=lambda phase: _update_repl_plan_phase(phase, repl_state),
        confirm_space_fn=turn_options.confirm_space_fn,
        tool_not_allowed_prompt_fn=turn_options.tool_not_allowed_prompt_fn,
        credential_profile_prompt_fn=turn_options.credential_profile_prompt_fn,
        serialized_turn_runner_factory=serialized_turn_runner_factory,
    )


def _supports_interactive_confirm() -> bool:
    """Return whether this CLI turn can safely prompt the user for a follow-up choice."""

    return bool(supports_interactive_tty())
