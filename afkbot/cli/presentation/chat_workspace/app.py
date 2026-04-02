"""Prompt-session chat workspace runtime for interactive chat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.patch_stdout import patch_stdout
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


@dataclass(slots=True)
class ChatWorkspaceChoiceState:
    """Interactive choice prompt state rendered inside the workspace prompt."""

    title: str
    prompt: str
    options: tuple[tuple[str, str], ...]
    default_index: int
    selected_index: int
    footer_lines: tuple[str, ...]


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
        self._choice_state: ChatWorkspaceChoiceState | None = None
        self._terminal_style: BaseStyle = build_chat_workspace_style()
        self._emit_output = supports_interactive_tty() if emit_output is None else emit_output
        self._last_rendered_entry_kind: str | None = None
        self._prompt_session = prompt_session or _build_prompt_session(
            completer=composer_completer,
            interactive_tty=self._emit_output,
            on_escape=self._handle_escape,
            choice_state_getter=lambda: self._choice_state,
            on_choice_previous=self._select_previous_choice,
            on_choice_next=self._select_next_choice,
            on_choice_submit=self._submit_choice_prompt,
            on_choice_cancel=self._cancel_choice_prompt,
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
        self._choice_state = ChatWorkspaceChoiceState(
            title=title,
            prompt=prompt,
            options=options,
            default_index=default_index,
            selected_index=default_index,
            footer_lines=footer_lines,
        )
        self._invalidate_prompt()
        try:
            while not self._exit_requested:
                raw_value = await self._prompt_choice_mode_input()
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
                self._invalidate_prompt()
            return None
        finally:
            self._choice_state = None
            self._invalidate_prompt()

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

        choice_state = self._choice_state
        footer_text = self._footer_text
        if choice_state is not None:
            footer_text = _choice_prompt_footer(
                footer_lines=choice_state.footer_lines,
                default_label=choice_state.options[choice_state.default_index][1],
            )
        draft_text = getattr(getattr(self._prompt_session, "default_buffer", None), "text", "")
        return ChatWorkspaceSnapshot(
            transcript_text=self._transcript.render_text(),
            status_text=self._status_text,
            queue_text=self._queue_text,
            footer_text=str(footer_text),
            overlay_title=None if choice_state is None else choice_state.title,
            draft_text=str(draft_text),
        )

    async def _prompt_choice_mode_input(self) -> str | None:
        prompt_async = getattr(self._prompt_session, "prompt_async", None)
        if not callable(prompt_async):
            return None
        original_completer = self._prompt_session.completer
        original_complete_while_typing = self._prompt_session.complete_while_typing
        original_auto_suggest = self._prompt_session.auto_suggest
        default_buffer = getattr(self._prompt_session, "default_buffer", None)
        saved_buffer_text = ""
        saved_cursor_position = 0
        if default_buffer is not None:
            saved_buffer_text = str(getattr(default_buffer, "text", ""))
            try:
                saved_cursor_position = int(getattr(default_buffer, "cursor_position", 0))
            except (TypeError, ValueError):
                saved_cursor_position = 0
            _set_buffer_draft(default_buffer, text="", cursor_position=0)
        try:
            self._prompt_session.completer = None
            self._prompt_session.complete_while_typing = False
            self._prompt_session.auto_suggest = None
            with patch_stdout():
                return cast(
                    str | None,
                    await prompt_async(self._prompt_message),
                )
        except EOFError:
            return None
        finally:
            self._prompt_session.completer = original_completer
            self._prompt_session.complete_while_typing = original_complete_while_typing
            self._prompt_session.auto_suggest = original_auto_suggest
            if default_buffer is not None:
                _set_buffer_draft(
                    default_buffer,
                    text=saved_buffer_text,
                    cursor_position=min(max(saved_cursor_position, 0), len(saved_buffer_text)),
                )

    def _prompt_message(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        _append_workspace_surface_lines(
            fragments,
            status_text=self._status_text,
            queue_text=self._queue_text,
        )
        choice_state = self._choice_state
        if choice_state is not None:
            fragments.extend(
                _choice_prompt_message(
                    title=choice_state.title,
                    prompt=choice_state.prompt,
                    options=choice_state.options,
                    selected_index=choice_state.selected_index,
                    default_index=choice_state.default_index,
                )
            )
            return fragments
        fragments.extend(
            [
                ("class:workspace.user-label", "you"),
                ("class:workspace.user-separator", " > "),
            ]
        )
        return fragments

    def _bottom_toolbar(self) -> StyleAndTextTuples:
        choice_state = self._choice_state
        if choice_state is not None:
            footer_text = _choice_prompt_footer(
                footer_lines=choice_state.footer_lines,
                default_label=choice_state.options[choice_state.default_index][1],
            )
            return [("class:workspace.footer-line", f" {footer_text}")]
        if not self._footer_text:
            return []
        return [("class:workspace.footer-line", f" {self._footer_text}")]

    def _handle_escape(self) -> None:
        if self._interrupt is None:
            return
        self._interrupt()

    def _select_previous_choice(self) -> None:
        self._move_choice_selection(-1)

    def _select_next_choice(self) -> None:
        self._move_choice_selection(1)

    def _move_choice_selection(self, delta: int) -> None:
        choice_state = self._choice_state
        if choice_state is None or len(choice_state.options) <= 1:
            return
        choice_state.selected_index = (choice_state.selected_index + delta) % len(
            choice_state.options
        )
        self._invalidate_prompt()

    def _submit_choice_prompt(self, event) -> None:  # type: ignore[no-untyped-def]
        choice_state = self._choice_state
        raw_value = event.app.current_buffer.text.strip()
        if raw_value:
            event.app.exit(result=raw_value)
            return
        if choice_state is not None and choice_state.options:
            selected_index = choice_state.selected_index % len(choice_state.options)
            event.app.exit(result=choice_state.options[selected_index][0])
            return
        event.app.exit(result="")

    def _cancel_choice_prompt(self, event) -> None:  # type: ignore[no-untyped-def]
        event.app.exit(exception=EOFError(), style="class:exiting")

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
    choice_state_getter: Callable[[], ChatWorkspaceChoiceState | None],
    on_choice_previous: Callable[[], None],
    on_choice_next: Callable[[], None],
    on_choice_submit: Callable[[Any], None],
    on_choice_cancel: Callable[[Any], None],
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
        "key_bindings": _build_main_prompt_bindings(
            on_escape=on_escape,
            choice_state_getter=choice_state_getter,
            on_choice_previous=on_choice_previous,
            on_choice_next=on_choice_next,
            on_choice_submit=on_choice_submit,
            on_choice_cancel=on_choice_cancel,
        ),
        "refresh_interval": 0.25,
    }
    if not interactive_tty:
        prompt_kwargs["input"] = DummyInput()
        prompt_kwargs["output"] = DummyOutput()
    session = PromptSession[str](**prompt_kwargs)
    session.bottom_toolbar = lambda: cast(AnyFormattedText, [])
    return session


def _build_main_prompt_bindings(
    *,
    on_escape: Callable[[], None],
    choice_state_getter: Callable[[], ChatWorkspaceChoiceState | None],
    on_choice_previous: Callable[[], None],
    on_choice_next: Callable[[], None],
    on_choice_submit: Callable[[Any], None],
    on_choice_cancel: Callable[[Any], None],
) -> KeyBindings:
    bindings = KeyBindings()

    choice_active = Condition(lambda: choice_state_getter() is not None)

    @bindings.add("up", filter=choice_active)
    def _move_previous(_event) -> None:  # type: ignore[no-untyped-def]
        on_choice_previous()

    @bindings.add("down", filter=choice_active)
    def _move_next(_event) -> None:  # type: ignore[no-untyped-def]
        on_choice_next()

    @bindings.add("enter", filter=choice_active)
    def _accept_choice(event) -> None:  # type: ignore[no-untyped-def]
        on_choice_submit(event)

    @bindings.add("escape", filter=choice_active)
    def _cancel_choice(event) -> None:  # type: ignore[no-untyped-def]
        on_choice_cancel(event)

    @bindings.add("escape")
    def _handle_escape(event) -> None:  # type: ignore[no-untyped-def]
        buffer = event.app.current_buffer
        if buffer.complete_state is not None:
            buffer.cancel_completion()
            event.app.invalidate()
            return
        on_escape()

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
        default_suffix = " (default)" if index == default_index else ""
        is_selected = index == selected_index
        marker = ">" if is_selected else " "
        mark = "(*)" if is_selected else "( )"
        style = "class:workspace.thinking" if is_selected else "class:workspace.notice"
        fragments.append((style, f"{marker} {mark} {label}{default_suffix}\n"))
    fragments.append(("", "\n> "))
    return fragments


def _choice_prompt_footer(
    *,
    footer_lines: tuple[str, ...],
    default_label: str,
) -> str:
    details = list(footer_lines)
    details.append(f"Blank selects default: {default_label}")
    return " · ".join(details)


def _append_workspace_surface_lines(
    fragments: StyleAndTextTuples,
    *,
    status_text: str,
    queue_text: str,
) -> None:
    if status_text:
        fragments.append(("class:workspace.status-line", status_text))
        fragments.append(("", "\n"))
    if queue_text:
        fragments.append(("class:workspace.queue-line", queue_text))
        fragments.append(("", "\n"))


def _set_buffer_draft(buffer: object, *, text: str, cursor_position: int) -> None:
    """Best-effort draft mutation for PromptSession buffers and test doubles."""

    try:
        setattr(buffer, "text", text)
    except Exception:
        return
    try:
        setattr(buffer, "cursor_position", cursor_position)
    except Exception:
        return


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
