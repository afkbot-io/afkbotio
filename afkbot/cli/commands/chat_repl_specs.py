"""Shared command metadata for the interactive chat REPL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatReplCommandSpec:
    """Describe one local REPL command family and its slash alias."""

    local_command: str
    slash_command: str
    help_summary: str
    argument_hints: tuple[str, ...] = ()


_COMMAND_SPECS: tuple[ChatReplCommandSpec, ...] = (
    ChatReplCommandSpec(
        local_command="//help",
        slash_command="/help",
        help_summary="show the interactive chat controls",
    ),
    ChatReplCommandSpec(
        local_command="//status",
        slash_command="/status",
        help_summary="show current planning, thinking, queue, and catalog state",
    ),
    ChatReplCommandSpec(
        local_command="//activity",
        slash_command="/activity",
        help_summary="show the latest visible turn activity summary",
    ),
    ChatReplCommandSpec(
        local_command="//capabilities",
        slash_command="/capabilities",
        help_summary="inspect the current capability catalog",
        argument_hints=("all", "skills", "subagents", "apps", "mcp"),
    ),
    ChatReplCommandSpec(
        local_command="//plan",
        slash_command="/plan",
        help_summary="change plan mode or inspect the stored plan",
        argument_hints=("off", "auto", "on", "default", "show", "clear"),
    ),
    ChatReplCommandSpec(
        local_command="//thinking",
        slash_command="/thinking",
        help_summary="change reasoning level for the next turns",
        argument_hints=("low", "medium", "high", "very_high", "default"),
    ),
    ChatReplCommandSpec(
        local_command="//exit",
        slash_command="/exit",
        help_summary="leave the interactive chat session",
    ),
    ChatReplCommandSpec(
        local_command="//quit",
        slash_command="/quit",
        help_summary="leave the interactive chat session",
    ),
)


def chat_repl_command_specs() -> tuple[ChatReplCommandSpec, ...]:
    """Return the canonical local-command specification set."""

    return _COMMAND_SPECS


def chat_repl_local_commands() -> tuple[str, ...]:
    """Return all command spellings surfaced in prompt completion."""

    return tuple(
        command
        for spec in _COMMAND_SPECS
        for command in (spec.local_command, spec.slash_command)
    )


def chat_repl_primary_commands() -> tuple[str, ...]:
    """Return the canonical `//...` command spellings."""

    return tuple(spec.local_command for spec in _COMMAND_SPECS)


def chat_repl_slash_aliases() -> tuple[str, ...]:
    """Return the slash aliases surfaced in prompt completion."""

    return tuple(spec.slash_command for spec in _COMMAND_SPECS)


def chat_repl_local_command_arguments() -> dict[str, tuple[str, ...]]:
    """Return argument-completion hints for both local and slash command spellings."""

    mapping: dict[str, tuple[str, ...]] = {}
    for spec in _COMMAND_SPECS:
        if not spec.argument_hints:
            continue
        mapping[spec.local_command] = spec.argument_hints
        mapping[spec.slash_command] = spec.argument_hints
    return mapping


def chat_repl_command_metadata() -> dict[str, str]:
    """Return completion metadata for both command spellings."""

    metadata: dict[str, str] = {}
    for spec in _COMMAND_SPECS:
        metadata[spec.local_command] = spec.help_summary
        metadata[spec.slash_command] = spec.help_summary
    return metadata
