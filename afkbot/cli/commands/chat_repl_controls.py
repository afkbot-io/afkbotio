"""Local REPL command handling for interactive chat sessions."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.cli.commands.chat_planning import normalize_chat_planning_mode
from afkbot.cli.commands.chat_repl_specs import chat_repl_primary_commands
from afkbot.cli.presentation.chat_plan_renderer import render_chat_plan
from afkbot.cli.presentation.chat_plan_status import (
    plan_summary_for_chat_workspace,
    stored_plan_status_for_chat_workspace,
)
from afkbot.cli.presentation.chat_workspace.capabilities import render_capability_catalog
from afkbot.cli.presentation.chat_workspace.status import (
    activity_text_for_chat_workspace,
    help_text_for_chat_workspace,
    status_text_for_chat_workspace,
)
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.llm.reasoning import normalize_thinking_level


@dataclass(frozen=True, slots=True)
class ChatReplCommandResult:
    """Result of parsing one local REPL command."""

    consumed: bool
    exit_repl: bool = False
    message: str | None = None


def handle_chat_repl_local_command(
    raw_message: str,
    *,
    state: ChatReplSessionState,
) -> ChatReplCommandResult:
    """Parse and execute one local REPL control command."""

    stripped = raw_message.strip()
    command_body = _local_command_body(stripped)
    if command_body is None:
        return ChatReplCommandResult(consumed=False)

    parts = command_body.split()
    if not parts:
        return ChatReplCommandResult(
            consumed=True,
            message=help_text_for_chat_workspace(state),
        )
    command = parts[0].lower()
    args = parts[1:]

    if command in {"exit", "quit"}:
        return ChatReplCommandResult(consumed=True, exit_repl=True)
    if command == "help":
        return ChatReplCommandResult(consumed=True, message=help_text_for_chat_workspace(state))
    if command == "status":
        return ChatReplCommandResult(
            consumed=True,
            message=status_text_for_chat_workspace(state),
        )
    if command == "activity":
        return ChatReplCommandResult(consumed=True, message=activity_text_for_chat_workspace(state))
    if command == "cancel":
        return ChatReplCommandResult(consumed=True, message="No active turn to cancel.")
    if command == "capabilities":
        return _handle_capabilities_command(args=args, state=state)
    if command == "plan":
        return _handle_plan_command(args=args, state=state)
    if command == "thinking":
        return _handle_thinking_command(args=args, state=state)
    return ChatReplCommandResult(
        consumed=True,
        message=(
            f"Unknown local command: //{command}\nUse //help to list interactive chat controls."
        ),
    )


def _handle_plan_command(
    *,
    args: list[str],
    state: ChatReplSessionState,
) -> ChatReplCommandResult:
    if not args:
        return ChatReplCommandResult(
            consumed=True,
            message=(
                f"Current planning mode: {state.planning_mode}\n"
                f"Stored plan: {stored_plan_status_for_chat_workspace(state.latest_plan, phase=state.latest_plan_phase)}\n"
                f"Plan summary: {plan_summary_for_chat_workspace(state.latest_plan)}\n"
                "Use //plan off, //plan auto, //plan on, //plan default, //plan show, or //plan clear."
            ),
        )
    value = args[0].strip().lower()
    if value == "show":
        if len(args) > 1:
            return ChatReplCommandResult(consumed=True, message="Usage: //plan show")
        if state.latest_plan is None:
            return ChatReplCommandResult(
                consumed=True,
                message="No stored plan for this chat session.",
            )
        return ChatReplCommandResult(
            consumed=True,
            message=render_chat_plan(
                state.latest_plan,
                phase=state.latest_plan_phase,
                activity=_plan_activity_text(state),
                include_header=True,
                leading_blank_line=False,
                ansi=False,
            ),
        )
    if value == "clear":
        if len(args) > 1:
            return ChatReplCommandResult(consumed=True, message="Usage: //plan clear")
        state.latest_plan = None
        state.latest_plan_phase = None
        return ChatReplCommandResult(consumed=True, message="Stored plan cleared.")
    if len(args) > 1:
        return ChatReplCommandResult(
            consumed=True,
            message="Usage: //plan off|auto|on|default|show|clear",
        )
    if value in {"default", "inherit", "profile"}:
        state.planning_mode = state.default_planning_mode
        return ChatReplCommandResult(
            consumed=True,
            message=f"Planning mode reset to: {state.planning_mode}",
        )
    try:
        normalized = normalize_chat_planning_mode(value)
    except ValueError:
        return ChatReplCommandResult(
            consumed=True,
            message="planning mode must be one of: off, auto, on, default, show, clear",
        )
    if normalized is None:
        return ChatReplCommandResult(
            consumed=True,
            message="planning mode must be one of: off, auto, on, default, show, clear",
        )
    state.planning_mode = normalized
    return ChatReplCommandResult(
        consumed=True,
        message=f"Planning mode updated to: {state.planning_mode}",
    )


def _handle_thinking_command(
    *,
    args: list[str],
    state: ChatReplSessionState,
) -> ChatReplCommandResult:
    if not args:
        return ChatReplCommandResult(
            consumed=True,
            message=(
                f"Current thinking level: {state.thinking_level or 'default'}\n"
                "Use //thinking low|medium|high|very_high|default."
            ),
        )
    if len(args) > 1:
        return ChatReplCommandResult(
            consumed=True,
            message="Usage: //thinking low|medium|high|very_high|default",
        )
    value = args[0].strip().lower()
    if value in {"default", "inherit", "profile"}:
        state.thinking_level = state.default_thinking_level
        return ChatReplCommandResult(
            consumed=True,
            message=f"Thinking level reset to: {state.thinking_level or 'default'}",
        )
    try:
        resolved = normalize_thinking_level(value)
    except ValueError as exc:
        return ChatReplCommandResult(consumed=True, message=str(exc))
    state.thinking_level = resolved
    return ChatReplCommandResult(
        consumed=True,
        message=f"Thinking level updated to: {state.thinking_level or 'default'}",
    )


def _handle_capabilities_command(
    *,
    args: list[str],
    state: ChatReplSessionState,
) -> ChatReplCommandResult:
    if len(args) > 1:
        return ChatReplCommandResult(
            consumed=True,
            message="Usage: //capabilities [all|skills|subagents|apps|mcp]",
        )
    section = "all" if not args else args[0].strip().lower()
    if section not in {"all", "skills", "subagents", "apps", "mcp"}:
        return ChatReplCommandResult(
            consumed=True,
            message="capabilities target must be one of: all, skills, subagents, apps, mcp",
        )
    return ChatReplCommandResult(
        consumed=True,
        message=render_capability_catalog(catalog=state.latest_catalog, section=section),
    )


def _local_command_body(stripped: str) -> str | None:
    if stripped.startswith("//"):
        return stripped[2:]
    if not stripped.startswith("/"):
        return None
    body = stripped[1:]
    parts = body.split(maxsplit=1)
    if not parts:
        return None
    command = parts[0].strip().lower()
    if not command:
        return None
    if f"//{command}" not in chat_repl_primary_commands():
        return None
    return body


def _plan_activity_text(state: ChatReplSessionState) -> str | None:
    if state.latest_plan_phase != "executing":
        return None
    activity_message = activity_text_for_chat_workspace(state)
    prefix = "Latest activity\n- "
    if not activity_message.startswith(prefix):
        return None
    return activity_message.removeprefix(prefix)
