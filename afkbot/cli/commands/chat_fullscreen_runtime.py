"""CLI runtime glue for interactive prompt-session chat workspace sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from afkbot.cli.commands.chat_startup_notices import build_startup_assistant_outcome
from afkbot.cli.commands.chat_fullscreen_support import (
    FullscreenChatWorkspaceUX,
    build_workspace_turn_options,
    cancel_background_task,
    interrupt_action,
)
from afkbot.cli.commands.chat_repl_input import consume_chat_repl_input
from afkbot.cli.commands.chat_repl_specs import (
    chat_repl_command_metadata,
    chat_repl_local_command_arguments,
    chat_repl_local_commands,
)
from afkbot.cli.presentation.chat_workspace.app import ChatWorkspaceApp
from afkbot.cli.presentation.chat_workspace.composer import ChatPromptCompleter
from afkbot.cli.presentation.chat_workspace.presenter import (
    build_chat_workspace_notice_entry,
    build_chat_workspace_outcome_entry,
    build_chat_workspace_progress_entries,
    build_chat_workspace_surface_state,
    build_chat_workspace_toolbar_text,
)
from afkbot.cli.presentation.progress_timeline import ProgressTimelineState
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.interactive_resume import available_profile_choices
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.chat_session.activity_state import capture_chat_activity
from afkbot.services.chat_session.interrupts import run_turn_interruptibly
from afkbot.services.chat_session.input_catalog import ChatInputCatalog
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.chat_session.repl_controller import run_queueable_chat_session
from afkbot.services.chat_session.repl_input import ChatReplInputOutcome
from afkbot.services.chat_session.repl_queue import ChatReplTurnQueue
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import (
    ChatTurnInteractiveOptions,
    ChatTurnOutcome,
)

RunReplTurnFn = Callable[
    [str, Callable[[ProgressEvent], None], ChatReplSessionState, ChatTurnInteractiveOptions],
    Coroutine[Any, Any, ChatTurnOutcome],
]
RefreshCatalogFn = Callable[[], Coroutine[Any, Any, None]]


def _allow_background_input_during_turn(_state: ChatReplSessionState) -> bool:
    """Keep queued-input handling enabled during plan-first turns.

    Interactive prompts already pause the reader explicitly via the controller hook, so
    planning mode itself should not disable same-session queueing.
    """

    return True


async def run_fullscreen_chat_workspace_session(
    *,
    profile_id: str,
    session_id: str,
    repl_state: ChatReplSessionState,
    catalog_getter: Callable[[], ChatInputCatalog],
    refresh_catalog: RefreshCatalogFn,
    run_turn: RunReplTurnFn,
    startup_assistant_message: str | None = None,
) -> None:
    """Run the interactive chat session inside the prompt-session workspace."""

    session_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None

    def _interrupt() -> None:
        action = interrupt_action(
            active_turn=repl_state.active_turn,
            session_running=session_task is not None and not session_task.done(),
        )
        if action == "cancel_turn" and session_task is not None:
            session_task.cancel()
            return
        workspace.request_exit()

    workspace = ChatWorkspaceApp(
        composer_completer=ChatPromptCompleter(
            catalog_getter=catalog_getter,
            local_commands=chat_repl_local_commands(),
            local_command_arguments=chat_repl_local_command_arguments(),
            local_command_metadata=chat_repl_command_metadata(),
        ),
        interrupt=_interrupt,
    )
    ux = FullscreenChatWorkspaceUX()
    progress_timeline_state = ProgressTimelineState()
    progress_entries_emitted = False
    spinner_frames = ("◌", "◉", "◍", "◉")
    spinner_position = 0

    def _status_mode_icon() -> str | None:
        activity = repl_state.latest_activity
        if activity is None or not activity.running:
            return None
        if activity.stage == "thinking":
            return "◇"
        if activity.stage == "planning":
            return "◈"
        if activity.stage == "tool_call":
            return "⚙"
        if activity.stage == "subagent_wait":
            return "↻"
        return None

    def _build_status_marker(*, animate: bool) -> str | None:
        nonlocal spinner_position
        icon = _status_mode_icon()
        if icon is None:
            return None
        marker = f"{spinner_frames[spinner_position]} {icon}"
        if animate:
            spinner_position = (spinner_position + 1) % len(spinner_frames)
        return marker

    def _sync_workspace_from_state() -> None:
        workspace.replace_surface_state(
            build_chat_workspace_surface_state(
                repl_state,
                status_marker=_build_status_marker(animate=False),
            )
        )
        workspace.set_toolbar_text(build_chat_workspace_toolbar_text(repl_state))

    async def _read_input() -> str:
        return await workspace.read_submitted_message()

    def _consume_input(
        raw_message: str,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
    ) -> ChatReplInputOutcome:
        outcome = consume_chat_repl_input(
            raw_message=raw_message,
            repl_state=repl_state,
            turn_queue=turn_queue,
            turn_active=turn_active,
        )
        return outcome

    def _emit_notice(message: str) -> None:
        workspace.append_transcript_entry(build_chat_workspace_notice_entry(message))

    def _emit_turn_output(outcome: ChatTurnOutcome | None) -> None:
        entry = build_chat_workspace_outcome_entry(outcome)
        if entry is not None:
            workspace.append_transcript_entry(entry)

    def _emit_startup_assistant_message(message: str) -> None:
        _emit_turn_output(
            build_startup_assistant_outcome(
                message=message,
                profile_id=profile_id,
                session_id=session_id,
            )
        )

    async def _confirm_workspace_operation(
        *,
        question: str,
        default: bool,
        title: str,
        yes_label: str = "Approve",
        no_label: str = "Deny",
        hint_text: str | None = None,
        **_unused: object,
    ) -> bool:
        del _unused
        return await workspace.confirm(
            title=title,
            question=question,
            default=default,
            yes_label=yes_label,
            no_label=no_label,
            hint_text=hint_text,
            cancel_result=default,
        )

    async def _prompt_workspace_tool_access(
        *,
        envelope: ActionEnvelope,
        question_text: str,
        **_unused: object,
    ) -> str:
        del envelope, _unused
        selected = await workspace.choose_option(
            title="Tool access request",
            prompt=question_text,
            options=(
                ("allow_once", "Run once"),
                ("allow_session", "Allow for session"),
                ("deny", "Do not run"),
            ),
            default_value="deny",
            footer_lines=("↑/↓ move, Enter confirm, Esc cancel",),
        )
        return "deny" if selected is None else selected

    async def _prompt_workspace_credential_profile(envelope: ActionEnvelope) -> str | None:
        available_profiles = available_profile_choices(envelope)
        if not available_profiles:
            return None
        selected = await workspace.choose_option(
            title="Credential profile",
            prompt="Choose credential profile",
            options=tuple((profile, profile) for profile in available_profiles),
            default_value=available_profiles[0],
            footer_lines=("↑/↓ move, Enter confirm, Esc cancel",),
        )
        selected_profile = selected.strip() if selected is not None else ""
        if selected_profile in available_profiles:
            return selected_profile
        return None

    async def _present_plan(
        plan_result: TurnResult,
        plan_snapshot: ChatPlanSnapshot | None,
    ) -> None:
        _sync_workspace_from_state()
        outcome = ChatTurnOutcome(
            result=plan_result,
            plan_snapshot=plan_snapshot,
            final_output="plan",
        )
        _emit_turn_output(outcome)

    def _progress_sink(event: ProgressEvent) -> None:
        nonlocal progress_entries_emitted, progress_timeline_state
        activity = capture_chat_activity(event)
        if activity is not None and activity != repl_state.latest_activity:
            repl_state.latest_activity = activity
            _sync_workspace_from_state()
        progress_timeline_state, transcript_entries = build_chat_workspace_progress_entries(
            progress_timeline_state,
            event,
            first_progress_entry=not progress_entries_emitted,
        )
        for entry in transcript_entries:
            workspace.append_transcript_entry(entry)
        if transcript_entries:
            progress_entries_emitted = True

    async def _run_session() -> None:
        async def _run_workspace_turn(
            message: str,
            progress_sink: Callable[[ProgressEvent], None],
            state: ChatReplSessionState,
            turn_options: ChatTurnInteractiveOptions,
        ) -> ChatTurnOutcome:
            nonlocal progress_entries_emitted, progress_timeline_state
            progress_timeline_state = ProgressTimelineState()
            progress_entries_emitted = False
            return await run_turn(
                message,
                progress_sink,
                state,
                build_workspace_turn_options(
                    state=state,
                    turn_options=turn_options,
                    present_plan=_present_plan,
                    confirm_space_fn=_confirm_workspace_operation,
                    tool_not_allowed_prompt_fn=_prompt_workspace_tool_access,
                    credential_profile_prompt_fn=_prompt_workspace_credential_profile,
                ),
            )

        _sync_workspace_from_state()
        if startup_assistant_message:
            _emit_startup_assistant_message(startup_assistant_message)
        await run_queueable_chat_session(
            ux=ux,
            read_input=_read_input,
            run_turn=_run_workspace_turn,
            repl_state=repl_state,
            refresh_catalog=refresh_catalog,
            consume_input=_consume_input,
            progress_sink=_progress_sink,
            run_interruptible_turn=lambda run_turn_coro: run_turn_interruptibly(
                task_name=f"chat_repl_turn:{profile_id}:{session_id}",
                run_turn=run_turn_coro,
                on_interrupt=lambda: _emit_notice(
                    "Interrupt received. Cancelling current turn. Press Ctrl-C again to exit."
                ),
            ),
            emit_turn_output=_emit_turn_output,
            emit_notice=_emit_notice,
            on_state_change=lambda _state: _sync_workspace_from_state(),
            allow_background_input=_allow_background_input_during_turn,
        )
        workspace.request_exit()

    async def _run_heartbeat() -> None:
        while not workspace.exit_requested:
            if repl_state.active_turn:
                workspace.replace_surface_state(
                    build_chat_workspace_surface_state(
                        repl_state,
                        status_marker=_build_status_marker(animate=True),
                    )
                )
            await asyncio.sleep(0.35)

    session_task = asyncio.create_task(_run_session())
    heartbeat_task = asyncio.create_task(_run_heartbeat())
    try:
        await session_task
    finally:
        workspace.request_exit()
        await cancel_background_task(session_task)
        await cancel_background_task(heartbeat_task)
