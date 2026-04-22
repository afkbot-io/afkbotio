"""REPL transport helpers for interactive chat sessions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, cast

import typer

from afkbot.cli.commands.chat_planning import ChatPlanningMode
from afkbot.cli.commands.chat_fullscreen_runtime import (
    run_fullscreen_chat_workspace_session,
)
from afkbot.cli.commands.chat_startup_notices import render_startup_assistant_message
from afkbot.cli.commands.chat_task_startup_digest import (
    _DIGEST_TIMEOUT_SEC,
    compose_human_task_startup_message,
    render_human_task_startup_summary,
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
from afkbot.services.task_flow import get_task_flow_service
from afkbot.services.task_flow.human_ref import resolve_local_human_ref
from afkbot.settings import Settings

RunReplTurnFn = Callable[
    [str, Callable[[ProgressEvent], None], ChatReplSessionState, ChatTurnInteractiveOptions],
    Coroutine[Any, Any, ChatTurnOutcome],
]
RefreshCatalogFn = Callable[[], Coroutine[Any, Any, None]]


class _InterruptNotifiableUX(Protocol):
    """UX subset needed by the Ctrl-C notifier."""

    def stop_progress(self) -> None: ...


def run_repl_transport(
    *,
    profile_id: str,
    session_id: str,
    session_label: str | None = None,
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
            session_id=session_id,
            session_label=session_label,
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

        startup_assistant_message = runner.run(
            _load_task_startup_assistant_message(
                settings=settings,
                profile_id=profile_id,
            )
        )

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

                _invoke_repl_sequential(
                    runner=runner,
                    ux=ux,
                    profile_id=profile_id,
                    session_id=session_id,
                    session_label=session_label,
                    run_turn=run_turn,
                    repl_state=repl_state,
                    progress_sink=_sequential_progress_sink,
                    refresh_catalog=_refresh_catalog,
                    startup_assistant_message=startup_assistant_message,
                )
            else:
                runner.run(
                    _build_fullscreen_chat_workspace_session(
                        profile_id=profile_id,
                        session_id=session_id,
                        run_turn=run_turn,
                        repl_state=repl_state,
                        catalog_getter=catalog_store.current,
                        refresh_catalog=_refresh_catalog,
                        startup_assistant_message=startup_assistant_message,
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
    session_label: str | None = None,
    run_turn: RunReplTurnFn,
    repl_state: ChatReplSessionState,
    progress_sink: Callable[[ProgressEvent], None],
    refresh_catalog: Callable[[], Coroutine[Any, Any, None]],
    startup_assistant_message: str | None = None,
) -> None:
    """Run the sequential REPL path for non-interactive stdin/stdout."""

    turn_queue = ChatReplTurnQueue()
    session_banner = _render_repl_session_banner(
        session_id=session_id,
        session_label=session_label,
    )
    if session_banner is not None:
        typer.echo(session_banner)
    if startup_assistant_message:
        rendered_notice = render_startup_assistant_message(
            message=startup_assistant_message,
            profile_id=profile_id,
            session_id=session_id,
        )
        if rendered_notice is not None:
            typer.echo(rendered_notice)

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


def _invoke_repl_sequential(
    *,
    runner: asyncio.Runner,
    ux: InteractiveChatUX,
    profile_id: str,
    session_id: str,
    session_label: str | None,
    run_turn: RunReplTurnFn,
    repl_state: ChatReplSessionState,
    progress_sink: Callable[[ProgressEvent], None],
    refresh_catalog: RefreshCatalogFn,
    startup_assistant_message: str | None,
) -> None:
    """Call the sequential REPL runtime compatibly across adjacent versions."""

    kwargs: dict[str, object] = {
        "runner": runner,
        "ux": ux,
        "profile_id": profile_id,
        "session_id": session_id,
        "session_label": session_label,
        "run_turn": run_turn,
        "repl_state": repl_state,
        "progress_sink": progress_sink,
        "refresh_catalog": refresh_catalog,
        "startup_assistant_message": startup_assistant_message,
    }
    signature = inspect.signature(_run_repl_sequential)
    invoke = cast(Callable[..., None], _run_repl_sequential)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        invoke(**kwargs)
        return
    filtered_kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    invoke(**filtered_kwargs)


def _build_fullscreen_chat_workspace_session(
    *,
    profile_id: str,
    session_id: str,
    run_turn: RunReplTurnFn,
    repl_state: ChatReplSessionState,
    catalog_getter: Callable[[], Any],
    refresh_catalog: RefreshCatalogFn,
    startup_assistant_message: str | None,
) -> Coroutine[Any, Any, None]:
    """Build fullscreen REPL coroutine using only supported installed kwargs."""

    kwargs: dict[str, object] = {
        "profile_id": profile_id,
        "session_id": session_id,
        "run_turn": run_turn,
        "repl_state": repl_state,
        "catalog_getter": catalog_getter,
        "refresh_catalog": refresh_catalog,
        "startup_assistant_message": startup_assistant_message,
    }
    signature = inspect.signature(run_fullscreen_chat_workspace_session)
    invoke = cast(Callable[..., Coroutine[Any, Any, None]], run_fullscreen_chat_workspace_session)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return invoke(**kwargs)
    filtered_kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return invoke(**filtered_kwargs)


def _render_repl_session_banner(
    *,
    session_id: str,
    session_label: str | None,
) -> str | None:
    """Return one startup line that exposes the current chat session identity."""

    normalized_session_id = str(session_id).strip()
    if not normalized_session_id:
        return None
    normalized_label = str(session_label or "").strip()
    if normalized_label and normalized_label != normalized_session_id:
        return f"Session: {normalized_label} · id={normalized_session_id}"
    return f"Session: {normalized_session_id}"


async def _load_task_startup_assistant_message(
    *,
    settings: Settings,
    profile_id: str,
) -> str | None:
    """Build one assistant-style startup message for the current human task inbox, failing open."""

    owner_ref = _resolve_chat_human_owner_ref(settings)
    try:
        async with asyncio.timeout(2.0):
            service = get_task_flow_service(settings)
            summary = await service.summarize_human_tasks(
                profile_id=profile_id,
                owner_ref=owner_ref,
            )
            inbox = await service.build_human_inbox(
                profile_id=profile_id,
                owner_ref=owner_ref,
                task_limit=5,
                event_limit=3,
                channel="chat_startup",
                mark_seen=True,
            )
    except Exception:
        return None
    fallback_message = render_human_task_startup_summary(summary, settings=settings, inbox=inbox)
    if fallback_message is None:
        return None
    try:
        async with asyncio.timeout(_DIGEST_TIMEOUT_SEC):
            rendered = await compose_human_task_startup_message(
                settings=settings,
                profile_id=profile_id,
                summary=summary,
                inbox=inbox,
            )
    except Exception:
        return fallback_message
    return rendered or fallback_message


def _resolve_chat_human_owner_ref(settings: Settings) -> str:
    """Resolve the current local human owner reference for chat startup lookups."""

    return resolve_local_human_ref(settings)
