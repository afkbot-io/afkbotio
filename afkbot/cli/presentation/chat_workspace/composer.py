"""Prompt-toolkit composer helpers for the chat workspace."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from types import MappingProxyType

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from afkbot.services.chat_session.input_catalog import ChatInputCatalog


class ChatPromptCompleter(Completer):
    """Provide command, capability, and file completions for the chat composer."""

    def __init__(
        self,
        *,
        catalog_getter: Callable[[], ChatInputCatalog],
        local_commands: tuple[str, ...],
        local_command_arguments: dict[str, tuple[str, ...]] | None = None,
        local_command_metadata: dict[str, str] | None = None,
    ) -> None:
        self._catalog_getter = catalog_getter
        self._local_commands = local_commands
        self._local_command_arguments = MappingProxyType(local_command_arguments or {})
        self._local_command_metadata = MappingProxyType(local_command_metadata or {})

    def get_completions(self, document: Document, complete_event: object) -> Iterable[Completion]:
        """Yield completions for the token before the cursor."""

        _ = complete_event
        catalog = self._catalog_getter()
        argument_context = _local_command_argument_context(
            text_before_cursor=document.text_before_cursor,
            local_command_arguments=self._local_command_arguments,
        )
        if argument_context is not None:
            command, argument_prefix, start_position = argument_context
            return tuple(
                _command_argument_completions(
                    command=command,
                    argument_prefix=argument_prefix,
                    start_position=start_position,
                    local_command_arguments=self._local_command_arguments,
                )
            )
        token, token_start = _token_before_cursor(document.text_before_cursor)
        if not token:
            return ()
        if token.startswith("//"):
            return tuple(
                _command_completions(
                    token=token,
                    token_start=token_start,
                    commands=_command_scope(self._local_commands, slash_only=False),
                    command_metadata=self._local_command_metadata,
                )
            )
        if token.startswith("/"):
            return tuple(
                _command_completions(
                    token=token,
                    token_start=token_start,
                    commands=_command_scope(self._local_commands, slash_only=True),
                    command_metadata=self._local_command_metadata,
                )
            )
        if token.startswith("$"):
            return tuple(
                _capability_completions(
                    token=token,
                    token_start=token_start,
                    skill_names=catalog.skill_names,
                    subagent_names=catalog.subagent_names,
                    app_names=catalog.app_names,
                    mcp_server_names=catalog.mcp_server_names,
                    mcp_tool_names=catalog.mcp_tool_names,
                )
            )
        if token.startswith("@"):
            return tuple(
                _file_completions(
                    token=token,
                    token_start=token_start,
                    file_paths=catalog.file_paths,
                )
            )
        return ()

    def has_completion_context(self, text_before_cursor: str) -> bool:
        """Return whether the current draft grammar should open the completion menu."""

        argument_context = _local_command_argument_context(
            text_before_cursor=text_before_cursor,
            local_command_arguments=self._local_command_arguments,
        )
        if argument_context is not None:
            return True
        token, _token_start = _token_before_cursor(text_before_cursor)
        return bool(token) and token.startswith(("//", "/", "$", "@"))

def _token_before_cursor(text_before_cursor: str) -> tuple[str, int]:
    if not text_before_cursor:
        return "", 0
    if text_before_cursor[-1].isspace():
        return "", 0
    split_index = max(
        text_before_cursor.rfind(" "),
        text_before_cursor.rfind("\n"),
        text_before_cursor.rfind("\t"),
    )
    token = text_before_cursor[split_index + 1 :]
    return token, -(len(token))


def _local_command_argument_context(
    *,
    text_before_cursor: str,
    local_command_arguments: MappingProxyType[str, tuple[str, ...]],
) -> tuple[str, str, int] | None:
    stripped = text_before_cursor.lstrip()
    if not stripped.startswith("/"):
        return None
    if " " not in stripped:
        return None
    command, remainder = stripped.split(" ", 1)
    if command not in local_command_arguments:
        return None
    argument_prefix = remainder.lstrip()
    if " " in argument_prefix:
        return None
    return command, argument_prefix, -len(argument_prefix)


def _command_scope(commands: tuple[str, ...], *, slash_only: bool) -> tuple[str, ...]:
    if slash_only:
        return tuple(
            command
            for command in commands
            if command.startswith("/") and not command.startswith("//")
        )
    return tuple(command for command in commands if command.startswith("//"))


def _command_completions(
    *,
    token: str,
    token_start: int,
    commands: tuple[str, ...],
    command_metadata: MappingProxyType[str, str],
) -> Iterable[Completion]:
    prefix = token.lower()
    for command in commands:
        if not command.startswith(prefix):
            continue
        yield Completion(
            text=command,
            start_position=token_start,
            display=command,
            display_meta=command_metadata.get(command, "local"),
        )


def _command_argument_completions(
    *,
    command: str,
    argument_prefix: str,
    start_position: int,
    local_command_arguments: MappingProxyType[str, tuple[str, ...]],
) -> Iterable[Completion]:
    prefix = argument_prefix.lower()
    for argument in local_command_arguments.get(command, ()):
        if prefix and not argument.startswith(prefix):
            continue
        yield Completion(
            text=argument,
            start_position=start_position,
            display=argument,
            display_meta=command,
        )


def _capability_completions(
    *,
    token: str,
    token_start: int,
    skill_names: tuple[str, ...],
    subagent_names: tuple[str, ...],
    app_names: tuple[str, ...],
    mcp_server_names: tuple[str, ...],
    mcp_tool_names: tuple[str, ...],
) -> Iterable[Completion]:
    body = token[1:].lower()
    seen: set[str] = set()
    capability_items = (
        *((name, "skill") for name in skill_names),
        *((name, "subagent") for name in subagent_names),
        *((name, "app") for name in app_names),
        *((name, "mcp server") for name in mcp_server_names),
        *((name, "mcp tool") for name in mcp_tool_names),
    )
    for name, meta in capability_items:
        lowered = name.lower()
        if body and not lowered.startswith(body):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        yield Completion(
            text=f"${name}",
            start_position=token_start,
            display=f"${name}",
            display_meta=meta,
        )


def _file_completions(
    *,
    token: str,
    token_start: int,
    file_paths: tuple[str, ...],
) -> Iterable[Completion]:
    body = token[1:].lstrip("./").lower()
    for path in file_paths:
        lowered = path.lower()
        if body and body not in lowered:
            continue
        yield Completion(
            text=f"@{path}",
            start_position=token_start,
            display=f"@{path}",
            display_meta="file",
        )
