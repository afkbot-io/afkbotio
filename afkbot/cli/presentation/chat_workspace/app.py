"""Prompt-session chat workspace runtime for interactive chat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import BaseStyle

from afkbot.cli.presentation.chat_input import ChatInputReader
from afkbot.cli.presentation.chat_workspace.layout import (
    ChatWorkspaceSurfaceState,
    render_chat_workspace_surface_text,
)
from afkbot.cli.presentation.chat_workspace.theme import build_chat_workspace_style
from afkbot.cli.presentation.chat_workspace.toolbar import DEFAULT_CHAT_WORKSPACE_FOOTER
from afkbot.cli.presentation.chat_workspace.transcript import (
    ChatWorkspaceTranscript,
    ChatWorkspaceTranscriptEntry,
)
from afkbot.cli.presentation.tty import supports_interactive_tty


@dataclass(frozen=True, slots=True)
class ChatWorkspaceSnapshot:
    """Inspectable snapshot of current workspace text state."""

    transcript_text: str
    status_text: str
    queue_text: str
    footer_text: str
    overlay_title: str | None
    draft_text: str


class ChatWorkspaceApp:
    """Own prompt-session input plus append-only terminal transcript output."""

    def __init__(
        self,
        *,
        title: str = "AFK Chat Workspace",
        surface_state: ChatWorkspaceSurfaceState | None = None,
        composer_completer: Completer | None = None,
        interrupt: Callable[[], None] | None = None,
        prompt_session: PromptSession[str] | None = None,
        emit_output: bool | None = None,
    ) -> None:
        self._title = title
        self._interrupt = interrupt
        self._transcript = ChatWorkspaceTranscript()
        self._surface_state = surface_state or ChatWorkspaceSurfaceState()
        self._footer_text = DEFAULT_CHAT_WORKSPACE_FOOTER
        self._status_text = ""
        self._queue_text = ""
        self._exit_requested = False
        self._terminal_style: BaseStyle = build_chat_workspace_style()
        self._emit_output = supports_interactive_tty() if emit_output is None else emit_output
        self._last_rendered_entry_kind: str | None = None
        self._prompt_session = prompt_session or _build_prompt_session(
            completer=composer_completer,
            interactive_tty=self._emit_output,
            on_escape=self._handle_escape,
        )
        self._prompt_session.bottom_toolbar = self._bottom_toolbar
        self._input_reader = ChatInputReader(
            prompt_session=self._prompt_session,
            prompt_message=self._prompt_message,
        )
        self.replace_surface_state(self._surface_state)

    @property
    def title(self) -> str:
        """Return the workspace title."""

        return self._title

    @property
    def exit_requested(self) -> bool:
        """Return whether shutdown was requested."""

        return self._exit_requested

    async def read_submitted_message(self) -> str:
        """Read one interactive user message from the prompt session."""

        if self._exit_requested:
            raise EOFError
        return await self._input_reader.read_input()

    def append_transcript_entry(
        self,
        entry: ChatWorkspaceTranscriptEntry,
        *,
        echo: bool = True,
    ) -> None:
        """Record one transcript entry and optionally emit it to the terminal."""

        self._transcript.append(entry)
        if echo and self._emit_output:
            had_previous_render = self._last_rendered_entry_kind is not None
            print_formatted_text(
                FormattedText(
                    _render_terminal_entry(
                        entry,
                        had_previous=had_previous_render,
                        previous_kind=self._last_rendered_entry_kind,
                    )
                ),
                style=self._terminal_style,
                end="",
                flush=True,
            )
            self._last_rendered_entry_kind = entry.kind
        elif echo:
            self._last_rendered_entry_kind = entry.kind
        self._invalidate_prompt()

    def replace_surface_state(self, surface_state: ChatWorkspaceSurfaceState) -> None:
        """Replace the current status/queue surface state."""

        self._surface_state = surface_state
        self._status_text = render_chat_workspace_surface_text(surface_state.status_lines)
        self._queue_text = render_chat_workspace_surface_text(surface_state.queue_lines)
        self._invalidate_prompt()

    def set_toolbar_text(self, text: str) -> None:
        """Replace the footer/help text below the prompt."""

        self._footer_text = text
        self._invalidate_prompt()

    async def choose_option(
        self,
        *,
        title: str,
        prompt: str,
        options: tuple[tuple[str, str], ...],
        default_value: str | None = None,
        footer_lines: tuple[str, ...] = (),
    ) -> str | None:
        """Prompt for one choice using the bottom input line."""

        if not options:
            return None

        default_index = _default_choice_index(
            options=options,
            default_value=default_value,
        )
        option_values = tuple(value for value, _label in options)
        state: dict[str, int] = {"selected_index": default_index}
        while not self._exit_requested:
            message = _choice_prompt_message(
                title=title,
                prompt=prompt,
                options=options,
                selected_index=state["selected_index"],
                default_index=default_index,
            )
            footer = _choice_prompt_footer(
                footer_lines=footer_lines,
                default_label=options[default_index][1],
            )
            try:
                raw_value = await self._prompt_choice_input(
                    message=message,
                    footer=footer,
                    selected_index_ref=state,
                    option_values=option_values,
                )
            except TypeError:
                raw_value = await self._prompt_choice_input(
                    message=message,
                    footer=footer,
                )
            if raw_value is None:
                return None
            resolved = _resolve_choice_value(
                raw_value=raw_value,
                options=options,
                default_index=default_index,
            )
            if resolved is not None:
                return resolved
            if self._emit_output:
                print_formatted_text(
                    FormattedText(
                        [
                        (
                            "class:workspace.notice",
                            "Invalid choice. Enter a number, value, or leave blank for default.",
                        ),
                        ("", "\n"),
                        ]
                    ),
                    style=self._terminal_style,
                    end="",
                    flush=True,
                )
        return None

    async def confirm(
        self,
        *,
        title: str,
        question: str,
        default: bool,
        yes_label: str,
        no_label: str,
        hint_text: str | None = None,
        cancel_result: bool | None = None,
    ) -> bool:
        """Show one yes/no confirmation prompt."""

        selected = await self.choose_option(
            title=title,
            prompt=question,
            options=(("yes", yes_label), ("no", no_label)),
            default_value="yes" if default else "no",
            footer_lines=tuple(filter(None, (hint_text, "Enter choose · Esc cancel"))),
        )
        if selected is None:
            if cancel_result is not None:
                return cancel_result
            return default
        return selected == "yes"

    def request_exit(self) -> None:
        """Request shutdown and terminate any active prompt session."""

        self._exit_requested = True
        application = getattr(self._prompt_session, "app", None)
        if application is None or not application.is_running:
            return
        application.exit(exception=EOFError(), style="class:exiting")

    def snapshot(self) -> ChatWorkspaceSnapshot:
        """Capture current transcript, status, and footer text for tests."""

        return ChatWorkspaceSnapshot(
            transcript_text=self._transcript.render_text(),
            status_text=self._status_text,
            queue_text=self._queue_text,
            footer_text=self._footer_text,
            overlay_title=None,
            draft_text="",
        )

    async def _prompt_choice_input(
        self,
        *,
        message: AnyFormattedText,
        footer: AnyFormattedText,
        selected_index_ref: dict[str, int],
        option_values: tuple[str, ...],
    ) -> str | None:
        prompt_async = getattr(self._prompt_session, "prompt_async", None)
        if not callable(prompt_async):
            return None
        original_bottom_toolbar = self._prompt_session.bottom_toolbar
        original_key_bindings = self._prompt_session.key_bindings
        original_completer = self._prompt_session.completer
        original_complete_while_typing = self._prompt_session.complete_while_typing
        original_auto_suggest = self._prompt_session.auto_suggest
        try:
            return cast(
                str | None,
                await prompt_async(
                    message=message,
                    bottom_toolbar=footer,
                    completer=None,
                    complete_while_typing=False,
                    auto_suggest=None,
                    key_bindings=_build_choice_prompt_bindings(
                        selected_index_ref=selected_index_ref,
                        option_values=option_values,
                    ),
                ),
            )
        except EOFError:
            return None
        finally:
            self._prompt_session.bottom_toolbar = original_bottom_toolbar
            self._prompt_session.key_bindings = original_key_bindings
            self._prompt_session.completer = original_completer
            self._prompt_session.complete_while_typing = original_complete_while_typing
            self._prompt_session.auto_suggest = original_auto_suggest

    def _prompt_message(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if self._status_text:
            fragments.append(("class:workspace.status-line", self._status_text))
            fragments.append(("", "\n"))
        if self._queue_text:
            fragments.append(("class:workspace.queue-line", self._queue_text))
            fragments.append(("", "\n"))
        fragments.extend(
            [
                ("class:workspace.user-label", "you"),
                ("class:workspace.user-separator", " > "),
            ]
        )
        return fragments

    def _bottom_toolbar(self) -> StyleAndTextTuples:
        if not self._footer_text:
            return []
        return [("class:workspace.footer-line", f" {self._footer_text}")]

    def _handle_escape(self) -> None:
        if self._interrupt is None:
            return
        self._interrupt()

    def _invalidate_prompt(self) -> None:
        application = getattr(self._prompt_session, "app", None)
        if application is None or not application.is_running:
            return
        try:
            application.invalidate()
        except RuntimeError:
            return


def _build_prompt_session(
    *,
    completer: Completer | None,
    interactive_tty: bool,
    on_escape: Callable[[], None],
) -> PromptSession[str]:
    prompt_kwargs: dict[str, Any] = {
        "message": "",
        "bottom_toolbar": None,
        "multiline": False,
        "wrap_lines": False,
        "complete_while_typing": True,
        "auto_suggest": AutoSuggestFromHistory(),
        "history": InMemoryHistory(),
        "style": build_chat_workspace_style(),
        "completer": completer,
        "key_bindings": _build_main_prompt_bindings(on_escape=on_escape),
        "refresh_interval": 0.25,
    }
    if not interactive_tty:
        prompt_kwargs["input"] = DummyInput()
        prompt_kwargs["output"] = DummyOutput()
    session = PromptSession[str](**prompt_kwargs)
    session.bottom_toolbar = lambda: cast(AnyFormattedText, [])
    return session


def _build_main_prompt_bindings(*, on_escape: Callable[[], None]) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("escape")
    def _handle_escape(event) -> None:  # type: ignore[no-untyped-def]
        buffer = event.app.current_buffer
        if buffer.complete_state is not None:
            buffer.cancel_completion()
            event.app.invalidate()
            return
        on_escape()

    return bindings


def _build_choice_prompt_bindings(
    *,
    selected_index_ref: dict[str, int],
    option_values: tuple[str, ...],
) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("up")
    def _move_previous(event) -> None:  # type: ignore[no-untyped-def]
        options_len = len(option_values)
        if options_len <= 1:
            return
        current = selected_index_ref.get("selected_index", 0)
        next_index = (current - 1) % options_len
        selected_index_ref["selected_index"] = next_index
        event.app.invalidate()

    @bindings.add("down")
    def _move_next(event) -> None:  # type: ignore[no-untyped-def]
        options_len = len(option_values)
        if options_len <= 1:
            return
        current = selected_index_ref.get("selected_index", 0)
        next_index = (current + 1) % options_len
        selected_index_ref["selected_index"] = next_index
        event.app.invalidate()

    @bindings.add("enter")
    def _accept_choice(event) -> None:  # type: ignore[no-untyped-def]
        raw_value = event.app.current_buffer.text.strip()
        selected_index = int(selected_index_ref.get("selected_index", 0))
        if raw_value:
            event.app.exit(result=raw_value)
            return
        if option_values:
            selected_index = selected_index % len(option_values)
            event.app.exit(result=option_values[selected_index])
            return
        event.app.exit(result="")

    @bindings.add("escape")
    def _cancel_choice(event) -> None:  # type: ignore[no-untyped-def]
        event.app.exit(exception=EOFError(), style="class:exiting")

    return bindings


def _choice_prompt_message(
    *,
    title: str,
    prompt: str,
    options: tuple[tuple[str, str], ...],
    selected_index: int,
    default_index: int,
) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = [
        ("class:workspace.plan-title", title),
        ("", "\n"),
        ("class:workspace.assistant", prompt),
        ("", "\n\n"),
    ]
    for index, (_value, label) in enumerate(options):
        option_number = index + 1
        default_suffix = " (default)" if index == default_index else ""
        is_selected = index == selected_index
        marker = "▸" if is_selected else " "
        mark = "[x]" if is_selected else "[ ]"
        fragments.append(
            (
                "class:workspace.notice",
                f"{marker} {option_number}. {mark} {label}{default_suffix}\n",
            )
        )
    fragments.append(("", "\nselect > "))
    return fragments


def _choice_prompt_footer(
    *,
    footer_lines: tuple[str, ...],
    default_label: str,
) -> StyleAndTextTuples:
    details = list(footer_lines)
    details.append(f"Blank selects default: {default_label}")
    return [("class:workspace.footer-line", " · ".join(details))]


def _default_choice_index(
    *,
    options: tuple[tuple[str, str], ...],
    default_value: str | None,
) -> int:
    if default_value is None:
        return 0
    for index, (value, _label) in enumerate(options):
        if value == default_value:
            return index
    return 0


def _resolve_choice_value(
    *,
    raw_value: str,
    options: tuple[tuple[str, str], ...],
    default_index: int,
) -> str | None:
    normalized = raw_value.strip()
    if not normalized:
        return options[default_index][0]
    lowered = normalized.lower()
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(options):
            return options[index][0]
    for value, label in options:
        if lowered in {value.lower(), label.lower()}:
            return value
    if len(options) == 2 and {options[0][0], options[1][0]} == {"yes", "no"}:
        if lowered in {"y", "yes"}:
            return "yes"
        if lowered in {"n", "no"}:
            return "no"
    return None


def _render_terminal_entry(
    entry: ChatWorkspaceTranscriptEntry,
    *,
    had_previous: bool,
    previous_kind: str | None = None,
) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    if had_previous and entry.spacing_before != "tight":
        fragments.append(("", "\n"))
    lines = _normalized_lines(
        entry.text,
        preserve_blank_lines=entry.kind == "plan",
    )

    if entry.kind == "user":
        fragments.extend(_render_user_block(lines))
        return fragments
    if entry.kind == "plan":
        title = entry.title or "Proposed Plan"
        fragments.append(("class:workspace.plan-title", title))
        if lines:
            fragments.append(("", "\n"))
            for index, line in enumerate(lines):
                if index > 0:
                    fragments.append(("", "\n"))
                fragments.append(("class:workspace.plan-text", line))
        fragments.append(("", "\n"))
        return fragments

    style = _style_class_for_entry(entry)
    rendered_lines = lines
    if entry.kind in {"notice", "activity", "system"} and lines:
        rendered_lines = tuple(
            f"• {line}" if index == 0 else f"  {line}"
            for index, line in enumerate(lines)
        )
    for index, line in enumerate(rendered_lines):
        if index > 0:
            fragments.append(("", "\n"))
        fragments.append((style, line))
    trailing_newlines = 2 if entry.kind == "assistant" and entry.accent is None else 1
    for _ in range(trailing_newlines):
        fragments.append(("", "\n"))
    return fragments


def _render_user_block(lines: tuple[str, ...]) -> StyleAndTextTuples:
    if not lines:
        return [
            ("class:workspace.user-label", "you"),
            ("class:workspace.user-separator", " > "),
            ("", "\n"),
        ]
    fragments: StyleAndTextTuples = []
    for index, line in enumerate(lines):
        if index > 0:
            fragments.append(("", "\n"))
        if index == 0:
            fragments.append(("class:workspace.user-label", "you"))
            fragments.append(("class:workspace.user-separator", " > "))
            fragments.append(("class:workspace.user-text", line))
        else:
            fragments.append(("class:workspace.user-separator", "      "))
            fragments.append(("class:workspace.user-text", line))
    fragments.append(("", "\n"))
    return fragments


def _style_class_for_entry(entry: ChatWorkspaceTranscriptEntry) -> str:
    if entry.accent == "thinking":
        return "class:workspace.thinking"
    if entry.accent == "planning":
        return "class:workspace.planning"
    if entry.accent == "tool":
        return "class:workspace.tool"
    if entry.accent == "success":
        return "class:workspace.success"
    if entry.accent == "error":
        return "class:workspace.error"
    if entry.accent == "detail":
        return "class:workspace.detail"
    if entry.kind in {"notice", "activity", "system"}:
        return "class:workspace.notice"
    return "class:workspace.assistant"


def _normalized_lines(
    text: str,
    *,
    preserve_blank_lines: bool = False,
) -> tuple[str, ...]:
    stripped_text = text.strip()
    if not stripped_text:
        return ()

    collapsed_lines: list[str] = []
    last_was_blank = False
    for raw_line in stripped_text.splitlines():
        normalized_line = raw_line.rstrip()
        if not normalized_line.strip():
            if preserve_blank_lines and collapsed_lines and not last_was_blank:
                collapsed_lines.append("")
                last_was_blank = True
            continue
        collapsed_lines.append(normalized_line)
        last_was_blank = False
    return tuple(collapsed_lines)
