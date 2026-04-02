"""Inline CLI selectors for interactive prompt flows."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Final


def run_inline_single_select(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_value: str,
    hint_text: str | None = None,
) -> str | None:
    """Render one in-terminal single-select driven by arrows and enter."""

    if not options:
        return None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    if _has_running_event_loop():
        return _run_inline_single_select_text(
            title=title,
            text=text,
            options=options,
            default_value=default_value,
            hint_text=hint_text,
        )
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.styles import Style
    except ImportError:
        return _run_inline_single_select_text(
            title=title,
            text=text,
            options=options,
            default_value=default_value,
            hint_text=hint_text,
        )

    values = [value for value, _label in options]
    default_index = values.index(default_value) if default_value in values else 0
    state: dict[str, int] = {"cursor": default_index}
    option_width = _single_select_option_width(options)

    style = Style.from_dict(
        {
            "title": "bold",
            "hint": "ansigray",
            "focused": "ansicyan bold",
        }
    )

    def _render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [("class:title", f"{title}\n"), ("", f"{text}\n\n")]
        for index, (_value, label) in enumerate(options):
            is_focused = index == state["cursor"]
            pointer = "> " if is_focused else "  "
            mark = "(*) " if is_focused else "( ) "
            style_name = "class:focused" if is_focused else ""
            lines.append(
                (style_name, _pad_option_line(f"{pointer}{mark}{label}", option_width) + "\n")
            )
        lines.append(("class:hint", f"\n{hint_text or _HINT_TEXT}"))
        return lines

    body = Window(
        content=FormattedTextControl(_render),
        always_hide_cursor=True,
        dont_extend_height=True,
    )
    root = HSplit([body])
    kb = KeyBindings()

    @kb.add("up")
    def _up(event: object) -> None:
        state["cursor"] = (state["cursor"] - 1) % len(options)
        getattr(event, "app").invalidate()

    @kb.add("down")
    def _down(event: object) -> None:
        state["cursor"] = (state["cursor"] + 1) % len(options)
        getattr(event, "app").invalidate()

    @kb.add(" ")
    def _space(_event: object) -> None:
        return None

    @kb.add("enter")
    def _enter(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=values[state["cursor"]])

    @kb.add("escape")
    def _escape(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=None)

    @kb.add("c-c")
    def _ctrl_c(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=None)

    app: Any = Application(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )
    try:
        with patch_stdout():
            result = app.run()
    except Exception:
        return _run_inline_single_select_text(
            title=title,
            text=text,
            options=options,
            default_value=default_value,
            hint_text=hint_text,
        )
    return str(result) if isinstance(result, str) else None


async def run_inline_single_select_async(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_value: str,
    hint_text: str | None = None,
) -> str | None:
    """Async single-select variant used from async contexts."""

    if not options:
        return None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    if _has_running_event_loop():
        return await _run_inline_single_select_async(
            title=title,
            text=text,
            options=options,
            default_value=default_value,
            hint_text=hint_text,
        )
    return _run_inline_single_select_text(
        title=title,
        text=text,
        options=options,
        default_value=default_value,
        hint_text=hint_text,
    )


async def _run_inline_single_select_async(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_value: str,
    hint_text: str | None,
) -> str | None:
    """Async prompt-toolkit single-select implementation with async event loop."""

    values = [value for value, _label in options]
    default_index = values.index(default_value) if default_value in values else 0
    state: dict[str, int] = {"cursor": default_index}
    option_width = _single_select_option_width(options)

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.styles import Style
    except ImportError:
        return await asyncio.to_thread(
            _run_inline_single_select_text,
            title=title,
            text=text,
            options=options,
            default_value=default_value,
            hint_text=hint_text,
        )

    style = Style.from_dict(
        {
            "title": "bold",
            "hint": "ansigray",
            "focused": "ansicyan bold",
        }
    )

    def _render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [
            ("class:title", f"{title}\n"),
            ("", f"{text}\n\n"),
        ]
        for index, (_value, label) in enumerate(options):
            is_focused = index == state["cursor"]
            pointer = "> " if is_focused else "  "
            mark = "(*) " if is_focused else "( ) "
            style_name = "class:focused" if is_focused else ""
            lines.append(
                (style_name, _pad_option_line(f"{pointer}{mark}{label}", option_width) + "\n")
            )
        lines.append(("class:hint", f"\n{hint_text or _HINT_TEXT}"))
        return lines

    body = Window(
        content=FormattedTextControl(_render),
        always_hide_cursor=True,
        dont_extend_height=True,
    )
    root = HSplit([body])
    kb = KeyBindings()

    @kb.add("up")
    def _up(event: object) -> None:
        state["cursor"] = (state["cursor"] - 1) % len(options)
        getattr(event, "app").invalidate()

    @kb.add("down")
    def _down(event: object) -> None:
        state["cursor"] = (state["cursor"] + 1) % len(options)
        getattr(event, "app").invalidate()

    @kb.add(" ")
    def _space(_event: object) -> None:
        return None

    @kb.add("enter")
    def _enter(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=values[state["cursor"]])

    @kb.add("escape")
    def _escape(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=None)

    @kb.add("c-c")
    def _ctrl_c(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=None)

    app: Any = Application(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )
    try:
        with patch_stdout():
            result = await app.run_async()
    except Exception:
        return await asyncio.to_thread(
            _run_inline_single_select_text,
            title=title,
            text=text,
            options=options,
            default_value=default_value,
            hint_text=hint_text,
        )
    return str(result) if isinstance(result, str) else None


def select_option_dialog(
    *,
    title: str,
    text: str,
    options: list[str],
    default: str,
    hint_text: str | None = None,
) -> str:
    """Show one inline option selector and return selected option or default."""

    selected = run_inline_single_select(
        title=title,
        text=text,
        options=[(item, item) for item in options],
        default_value=default,
        hint_text=hint_text,
    )
    return str(selected).strip() if selected else default


def confirm_space(
    *,
    question: str,
    default: bool,
    title: str,
    yes_label: str = "Yes",
    no_label: str = "No",
    hint_text: str | None = None,
) -> bool:
    """Ask confirm prompt using inline selector and return normalized boolean answer."""

    default_value = "yes" if default else "no"
    selected = run_inline_single_select(
        title=title,
        text=question,
        options=[("yes", yes_label), ("no", no_label)],
        default_value=default_value,
        hint_text=hint_text,
    )
    if selected is None:
        return default
    return selected == "yes"


_HINT_TEXT: Final[str] = "↑/↓ move, Enter confirm, Esc cancel"


def run_inline_multi_select(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_values: tuple[str, ...],
    hint_text: str | None = None,
) -> list[str] | None:
    """Render in-terminal multi-select with checkbox toggles."""

    if not options:
        return []
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    if _has_running_event_loop():
        return _run_inline_multi_select_text(
            title=title,
            text=text,
            options=options,
            default_values=default_values,
            hint_text=hint_text,
        )
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.styles import Style
    except ImportError:
        return _run_inline_multi_select_text(
            title=title,
            text=text,
            options=options,
            default_values=default_values,
            hint_text=hint_text,
        )

    values = [value for value, _label in options]
    selected_values = {value for value in default_values if value in values}
    state: dict[str, int] = {"cursor": 0}
    option_width = _multi_select_option_width(options)

    style = Style.from_dict(
        {
            "title": "bold",
            "hint": "ansigray",
            "focused": "ansicyan bold",
        }
    )

    def _render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [("class:title", f"{title}\n"), ("", f"{text}\n\n")]
        for index, (value, label) in enumerate(options):
            is_focused = index == state["cursor"]
            is_selected = value in selected_values
            pointer = "> " if is_focused else "  "
            mark = "[x]" if is_selected else "[ ]"
            style_name = "class:focused" if is_focused else ""
            lines.append(
                (style_name, _pad_option_line(f"{pointer}{mark} {label}", option_width) + "\n")
            )
        lines.append(("class:hint", f"\n{hint_text or _MULTI_HINT_TEXT}"))
        return lines

    body = Window(
        content=FormattedTextControl(_render),
        always_hide_cursor=True,
        dont_extend_height=True,
    )
    root = HSplit([body])
    kb = KeyBindings()

    @kb.add("up")
    def _up(event: object) -> None:
        state["cursor"] = (state["cursor"] - 1) % len(options)
        getattr(event, "app").invalidate()

    @kb.add("down")
    def _down(event: object) -> None:
        state["cursor"] = (state["cursor"] + 1) % len(options)
        getattr(event, "app").invalidate()

    @kb.add(" ")
    def _space(event: object) -> None:
        value = values[state["cursor"]]
        if value in selected_values:
            selected_values.remove(value)
        else:
            selected_values.add(value)
        getattr(event, "app").invalidate()

    @kb.add("a")
    def _all(event: object) -> None:
        if len(selected_values) == len(values):
            selected_values.clear()
        else:
            selected_values.clear()
            selected_values.update(values)
        getattr(event, "app").invalidate()

    @kb.add("enter")
    def _enter(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=[value for value in values if value in selected_values])

    @kb.add("escape")
    def _escape(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=None)

    @kb.add("c-c")
    def _ctrl_c(event: object) -> None:
        app = getattr(event, "app")
        app.exit(result=None)

    app: Any = Application(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )
    try:
        with patch_stdout():
            result = app.run()
    except Exception:
        return _run_inline_multi_select_text(
            title=title,
            text=text,
            options=options,
            default_values=default_values,
            hint_text=hint_text,
        )
    if result is None:
        return None
    if not isinstance(result, list):
        return []
    return [str(item) for item in result]


def select_multi_option_dialog(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_values: tuple[str, ...],
    hint_text: str | None = None,
) -> tuple[str, ...]:
    """Show inline multi-select dialog and return selected values or defaults."""

    selected = run_inline_multi_select(
        title=title,
        text=text,
        options=options,
        default_values=default_values,
        hint_text=hint_text,
    )
    if selected is None:
        return default_values
    return tuple(item for item in selected if item)


_MULTI_HINT_TEXT: Final[str] = "↑/↓ move, Space toggle, A all/none, Enter confirm, Esc cancel"

_TEXT_PROMPT_HINT: Final[str] = "Enter option number or value. Blank confirms the default. Type q to cancel."
_TEXT_MULTI_PROMPT_HINT: Final[str] = (
    "Enter comma-separated option numbers or values. Blank keeps defaults. "
    "Type all, none, or q."
)


def _single_select_option_width(options: list[tuple[str, str]]) -> int:
    """Return padded width for single-select option lines."""

    return max(len(f"> (*) {label}") for _value, label in options)


def _multi_select_option_width(options: list[tuple[str, str]]) -> int:
    """Return padded width for multi-select option lines."""

    return max(len(f"> [x] {label}") for _value, label in options)


def _pad_option_line(value: str, width: int) -> str:
    """Pad one rendered option line to a stable width for clean redraws."""

    return value.ljust(width)


def _has_running_event_loop() -> bool:
    """Return whether this sync helper is being called from inside an active asyncio loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_inline_single_select_text(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_value: str,
    hint_text: str | None,
) -> str | None:
    """Fallback line-based prompt when full-screen prompt-toolkit UI is unavailable."""

    values = [value for value, _label in options]
    default_index = values.index(default_value) if default_value in values else 0
    prompt_lines = [title, text, ""]
    for index, (value, label) in enumerate(options, start=1):
        suffix = " (default)" if value == values[default_index] else ""
        prompt_lines.append(f"{index}. {label}{suffix}")
    prompt_lines.append(hint_text or _TEXT_PROMPT_HINT)
    sys.stdout.write("\n".join(prompt_lines) + "\n")
    sys.stdout.flush()

    while True:
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not answer:
            return values[default_index]
        if answer.lower() in {"q", "quit", "cancel"}:
            return None
        matched = _match_single_option(answer=answer, options=options)
        if matched is not None:
            return matched
        sys.stdout.write("Invalid choice. Try again or type q to cancel.\n")
        sys.stdout.flush()


def _run_inline_multi_select_text(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default_values: tuple[str, ...],
    hint_text: str | None,
) -> list[str] | None:
    """Fallback line-based multi-select when prompt-toolkit UI cannot be used safely."""

    values = [value for value, _label in options]
    selected_defaults = [value for value in values if value in set(default_values)]
    prompt_lines = [title, text, ""]
    for index, (value, label) in enumerate(options, start=1):
        suffix = " [default]" if value in selected_defaults else ""
        prompt_lines.append(f"{index}. {label}{suffix}")
    prompt_lines.append(hint_text or _TEXT_MULTI_PROMPT_HINT)
    sys.stdout.write("\n".join(prompt_lines) + "\n")
    sys.stdout.flush()

    while True:
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not answer:
            return selected_defaults
        lowered = answer.lower()
        if lowered in {"q", "quit", "cancel"}:
            return None
        if lowered == "all":
            return list(values)
        if lowered == "none":
            return []
        parsed = _parse_multi_select_answer(answer=answer, options=options)
        if parsed is not None:
            return parsed
        sys.stdout.write("Invalid choice. Try again or type q to cancel.\n")
        sys.stdout.flush()


def _match_single_option(*, answer: str, options: list[tuple[str, str]]) -> str | None:
    """Resolve one line-based selection by index, value, or label."""

    stripped = answer.strip()
    if stripped.isdecimal():
        index = int(stripped) - 1
        if 0 <= index < len(options):
            return options[index][0]
    lowered = stripped.lower()
    for value, label in options:
        if lowered in {value.lower(), label.lower()}:
            return value
    return None


def _parse_multi_select_answer(
    *,
    answer: str,
    options: list[tuple[str, str]],
) -> list[str] | None:
    """Resolve comma-separated multi-select input by index, value, or label."""

    tokens = [token.strip() for token in answer.split(",")]
    if not tokens or any(not token for token in tokens):
        return None
    selected: list[str] = []
    for token in tokens:
        matched = _match_single_option(answer=token, options=options)
        if matched is None:
            return None
        if matched not in selected:
            selected.append(matched)
    return selected
