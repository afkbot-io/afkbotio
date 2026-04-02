"""REPL transport helpers for interactive chat sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

import typer

from afkbot.cli.commands.chat_planning import ChatPlanningMode
from afkbot.cli.commands.chat_fullscreen_runtime import (
    run_fullscreen_chat_workspace_session,
)
from afkbot.cli.commands.chat_repl_input import consume_chat_repl_input
from afkbot.cli.presentation.chat_interactive import InteractiveChatUX
from afkbot.cli.presentation.chat_workspace.runtime import (
    build_chat_workspace_catalog_store,
    build_chat_workspace_catalog_refresher,
)
from afkbot.cli.presentation.chat_turn_output import render_chat_turn_outcome
from afkbot.cli.presentation.tty import supports_interactive_tty
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.browser_sessions import BrowserSessionManager
from afkbot.services.chat_session.activity_state import capture_chat_activity, starting_chat_activity
from afkbot.services.chat_session.interrupts import run_turn_interruptibly
from afkbot.services.chat_session.repl_queue import ChatReplTurnQueue
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnInteractiveOptions, ChatTurnOutcome
from afkbot.services.llm.reasoning import ThinkingLevel
from afkbot.settings import Settings

RunReplTurnFn = Callable[
    [str, Callable[[ProgressEvent], None], ChatReplSessionState, ChatTurnInteractiveOptions],
    Coroutine[Any, Any, ChatTurnOutcome],
]


class _InterruptNotifiableUX(Protocol):
    """UX subset needed by the Ctrl-C notifier."""

    def stop_progress(self) -> None: ...


def run_repl_transport(
    *,
    profile_id: str,
    session_id: str,
    run_turn: RunReplTurnFn,
    get_browser_session_manager: Callable[[], BrowserSessionManager],
    get_settings: Callable[[], Settings],
    planning_mode: ChatPlanningMode,
    thinking_level: ThinkingLevel | None,
) -> None:
    """Run the interactive chat REPL transport until explicit exit or EOF."""

    settings = get_settings()
    with asyncio.Runner() as runner:
        repl_state = ChatReplSessionState(
            planning_mode=planning_mode,
            thinking_level=thinking_level,
            default_planning_mode=planning_mode,
            default_thinking_level=thinking_level,
        )
        catalog_store = build_chat_workspace_catalog_store(
            runner=runner,
            settings=settings,
            profile_id=profile_id,
        )
        repl_state.latest_catalog = catalog_store.current()
        catalog_refresher = build_chat_workspace_catalog_refresher(
            settings=settings,
            profile_id=profile_id,
            catalog_store=catalog_store,
        )

        async def _refresh_catalog() -> None:
            await catalog_refresher()
            repl_state.latest_catalog = catalog_store.current()

        try:
            if not supports_interactive_tty():
                ux = InteractiveChatUX.create()

                def _sequential_progress_sink(event: ProgressEvent) -> None:
                    _record_progress(event=event, repl_state=repl_state, ux=ux)

                setattr(
                    _sequential_progress_sink,
                    "before_interactive_prompt",
                    ux.stop_progress,
                )

                _run_repl_sequential(
                    runner=runner,
                    ux=ux,
                    profile_id=profile_id,
                    session_id=session_id,
                    run_turn=run_turn,
                    repl_state=repl_state,
                    progress_sink=_sequential_progress_sink,
                    refresh_catalog=_refresh_catalog,
                )
            else:
                runner.run(
                    run_fullscreen_chat_workspace_session(
                        profile_id=profile_id,
                        session_id=session_id,
                        run_turn=run_turn,
                        repl_state=repl_state,
                        catalog_getter=catalog_store.current,
                        refresh_catalog=_refresh_catalog,
                    )
                )
        finally:
            runner.run(
                get_browser_session_manager().close_session(
                    root_dir=settings.root_dir,
                    profile_id=profile_id,
                    session_id=session_id,
                )
            )


def _run_repl_sequential(
    *,
    runner: asyncio.Runner,
    ux: InteractiveChatUX,
    profile_id: str,
    session_id: str,
    run_turn: RunReplTurnFn,
    repl_state: ChatReplSessionState,
    progress_sink: Callable[[ProgressEvent], None],
    refresh_catalog: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Run the sequential REPL path for non-interactive stdin/stdout."""

    turn_queue = ChatReplTurnQueue()

    while True:
        try:
            raw_message = ux.read_user_input()
        except (EOFError, KeyboardInterrupt):
            ux.stop_progress()
            return

        input_outcome = consume_chat_repl_input(
            raw_message=raw_message,
            repl_state=repl_state,
            turn_queue=turn_queue,
            turn_active=False,
            queue_messages=False,
        )
        if input_outcome.message:
            typer.echo(input_outcome.message)
        if input_outcome.notice:
            typer.echo(input_outcome.notice)
        if input_outcome.exit_repl:
            ux.stop_progress()
            return
        if input_outcome.consumed:
            continue

        try:
            runner.run(refresh_catalog())
            repl_state.active_turn = True
            repl_state.latest_activity = starting_chat_activity()
            ux.begin_agent_turn()
            result = runner.run(
                run_turn_interruptibly(
                    task_name=f"chat_repl_turn:{profile_id}:{session_id}",
                    run_turn=lambda: run_turn(
                        raw_message,
                        progress_sink,
                        repl_state,
                        ChatTurnInteractiveOptions(interactive_confirm=False),
                    ),
                    on_interrupt=_build_repl_interrupt_notifier(ux),
                )
            )
        except KeyboardInterrupt:
            ux.stop_progress()
            return
        except asyncio.CancelledError:
            ux.stop_progress()
            typer.echo("cancelled")
            raise typer.Exit(code=0) from None
        finally:
            repl_state.active_turn = False
            ux.stop_progress()
            runner.run(refresh_catalog())

        _echo_turn_outcome(result)


def _echo_turn_outcome(result: ChatTurnOutcome | None) -> None:
    """Render one completed turn outcome back into the CLI transcript."""

    if result is None:
        return
    rendered_output = render_chat_turn_outcome(
        result,
        include_header=False,
        leading_blank_line=False,
    )
    if rendered_output is not None:
        typer.echo(rendered_output)


def _record_progress(
    *,
    event: ProgressEvent,
    repl_state: ChatReplSessionState,
    ux: InteractiveChatUX,
) -> bool:
    """Persist one latest activity snapshot before rendering progress output."""

    activity_changed = False
    activity = capture_chat_activity(event)
    if activity is not None and activity != repl_state.latest_activity:
        repl_state.latest_activity = activity
        activity_changed = True
    ux.on_progress(event)
    return activity_changed


def _build_repl_interrupt_notifier(ux: _InterruptNotifiableUX) -> Callable[[], None]:
    """Return one small notifier used after the first Ctrl-C of an active turn."""

    def _notify() -> None:
        ux.stop_progress()
        typer.echo("  Interrupt received. Cancelling current turn. Press Ctrl-C again to exit.")

    return _notify
