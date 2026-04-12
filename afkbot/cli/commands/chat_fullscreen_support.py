"""Shared support helpers for the interactive chat workspace runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any, Literal

from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import (
    ChatTurnInteractiveOptions,
    PlanPresentationFn,
)


class FullscreenChatWorkspaceUX:
    """Minimal no-stdout UX adapter for prompt-session workspace sessions."""

    def begin_agent_turn(self) -> None:
        """A fullscreen workspace updates state in-place instead of using stdout spinners."""

    def on_progress(self, event: ProgressEvent) -> None:
        """Progress events are handled through panel state updates elsewhere."""

        _ = event

    def stop_progress(self) -> None:
        """Fullscreen workspace keeps no spinner-specific state."""


def interrupt_action(
    *,
    active_turn: bool,
    session_running: bool,
) -> Literal["cancel_turn", "exit_session"]:
    """Resolve one deterministic interrupt action from current session state."""

    if active_turn and session_running:
        return "cancel_turn"
    return "exit_session"


async def cancel_background_task(task: asyncio.Task[object] | None) -> None:
    """Cancel and await one workspace background task when it is still pending."""

    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def build_workspace_turn_options(
    state: ChatReplSessionState,
    turn_options: ChatTurnInteractiveOptions,
    *,
    present_plan: PlanPresentationFn,
    confirm_space_fn: Callable[..., bool | Coroutine[Any, Any, bool]] | None = None,
    tool_not_allowed_prompt_fn: Callable[..., str | Coroutine[Any, Any, str]] | None = None,
    credential_profile_prompt_fn: Callable[..., str | None | Coroutine[Any, Any, str | None]] | None = None,
) -> ChatTurnInteractiveOptions:
    """Attach only the workspace callbacks that differ from the default REPL wiring."""

    if (
        state.planning_mode != "on"
        and confirm_space_fn is None
        and tool_not_allowed_prompt_fn is None
        and credential_profile_prompt_fn is None
    ):
        return turn_options
    return ChatTurnInteractiveOptions(
        interactive_confirm=turn_options.interactive_confirm,
        prompt_to_plan_first=turn_options.prompt_to_plan_first,
        confirm_plan_execution=turn_options.confirm_plan_execution,
        present_plan=(
            present_plan
            if state.planning_mode == "on"
            else turn_options.present_plan
        ),
        confirm_space_fn=(
            confirm_space_fn
            if confirm_space_fn is not None
            else turn_options.confirm_space_fn
        ),
        tool_not_allowed_prompt_fn=(
            tool_not_allowed_prompt_fn
            if tool_not_allowed_prompt_fn is not None
            else turn_options.tool_not_allowed_prompt_fn
        ),
        credential_profile_prompt_fn=(
            credential_profile_prompt_fn
            if credential_profile_prompt_fn is not None
            else turn_options.credential_profile_prompt_fn
        ),
    )
