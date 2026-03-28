"""Status and help renderers for the interactive chat workspace."""

from __future__ import annotations

from afkbot.cli.commands.chat_repl_specs import (
    ChatReplCommandSpec,
    chat_repl_command_specs,
    chat_repl_primary_commands,
    chat_repl_slash_aliases,
)
from afkbot.cli.presentation.chat_workspace.capabilities import capability_catalog_summary
from afkbot.cli.presentation.chat_workspace.toolbar import build_chat_workspace_footer
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot
from afkbot.services.chat_session.session_state import ChatReplSessionState


def toolbar_text_for_chat_workspace(state: ChatReplSessionState) -> str:
    """Render the prompt toolbar status/help line for the current chat session."""

    return build_chat_workspace_footer(state)


def status_text_for_chat_workspace(state: ChatReplSessionState) -> str:
    """Render the detailed local control status block."""

    return (
        "Chat session controls\n"
        f"- planning_mode: {state.planning_mode}\n"
        f"- thinking_level: {state.thinking_level or 'default'}\n"
        f"- default_planning_mode: {state.default_planning_mode}\n"
        f"- default_thinking_level: {state.default_thinking_level or 'default'}\n"
        f"- active_turn: {state.active_turn}\n"
        f"- queued_messages: {state.queued_messages}\n"
        f"- stored_plan: {stored_plan_status_for_chat_workspace(state.latest_plan)}\n"
        f"- activity: {_activity_status(state)}\n"
        f"- capability_catalog: {capability_catalog_summary(state.latest_catalog)}\n"
        f"- local_commands: {', '.join(chat_repl_primary_commands())}\n"
        f"- slash_aliases: {', '.join(chat_repl_slash_aliases())}\n"
        "- inline_popup: type `/`, `$`, or `@` in the composer to open inline suggestions"
    )


def help_text_for_chat_workspace(state: ChatReplSessionState) -> str:
    """Render the interactive chat control help block."""

    lines = ["Interactive chat controls"]
    for spec in chat_repl_command_specs():
        lines.append(_help_line(spec, state))
    lines.append("- type `/` to open inline command suggestions in the composer")
    lines.append("- type `$` for capability suggestions and `@` for file suggestions")
    lines.append(f"- slash aliases are also available: {', '.join(chat_repl_slash_aliases())}")
    return "\n".join(lines)


def activity_text_for_chat_workspace(state: ChatReplSessionState) -> str:
    """Render the latest activity summary block."""

    return "Latest activity\n" f"- {_activity_status(state)}"


def stored_plan_status_for_chat_workspace(plan: ChatPlanSnapshot | None) -> str:
    """Render one short stored-plan summary for status surfaces."""

    if plan is None:
        return "none"
    if plan.step_count > 0:
        return f"{plan.step_count} step(s)"
    return "raw text"


def _activity_status(state: ChatReplSessionState) -> str:
    snapshot = state.latest_activity
    if snapshot is None:
        return "idle"
    parts = [snapshot.summary]
    if snapshot.detail:
        parts.append(f"detail={snapshot.detail}")
    parts.append(f"running={snapshot.running}")
    return " · ".join(parts)


def _help_line(spec: ChatReplCommandSpec, state: ChatReplSessionState) -> str:
    if spec.local_command == "//status":
        return (
            "- //status — show current planning/thinking settings "
            f"({state.planning_mode}, {state.thinking_level or 'default'})"
        )
    if not spec.argument_hints:
        return f"- {spec.local_command} — {spec.help_summary}"
    suffix = (
        f" [{'|'.join(spec.argument_hints)}]"
        if spec.local_command == "//capabilities"
        else f" {'|'.join(spec.argument_hints)}"
    )
    return f"- {spec.local_command}{suffix} — {spec.help_summary}"
