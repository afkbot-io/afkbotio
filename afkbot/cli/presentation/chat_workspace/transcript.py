"""Transcript models and styled renderers for the chat workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
import textwrap
from typing import Literal

from prompt_toolkit.formatted_text.base import StyleAndTextTuples

from afkbot.cli.presentation.terminal_text import sanitize_terminal_line

ChatWorkspaceTranscriptKind = Literal[
    "assistant",
    "user",
    "notice",
    "plan",
    "activity",
    "system",
]
ChatWorkspaceAccent = Literal[
    "thinking",
    "planning",
    "tool",
    "success",
    "error",
    "detail",
]
ChatWorkspaceSpacing = Literal["normal", "tight"]

_MIN_WRAP_WIDTH = 24
_USER_PREFIX = "you > "
_USER_LABEL = "you"
_USER_SEPARATOR = " > "
_CONTINUATION_PREFIX = " " * len(_USER_PREFIX)
_ACTIVITY_PREFIX = "• "


@dataclass(frozen=True, slots=True)
class ChatWorkspaceTranscriptEntry:
    """One rendered transcript block in the fullscreen chat workspace."""

    kind: ChatWorkspaceTranscriptKind
    text: str
    title: str | None = None
    accent: ChatWorkspaceAccent | None = None
    spacing_before: ChatWorkspaceSpacing = "normal"


@dataclass(frozen=True, slots=True)
class ChatWorkspaceRenderedTranscript:
    """Styled transcript output plus metadata used by the workspace layout."""

    plain_text: str
    fragments: StyleAndTextTuples
    line_count: int


@dataclass(slots=True)
class ChatWorkspaceTranscript:
    """Mutable transcript state for the fullscreen chat workspace."""

    entries: list[ChatWorkspaceTranscriptEntry] = field(default_factory=list)

    def append(self, entry: ChatWorkspaceTranscriptEntry) -> None:
        """Append one transcript entry."""

        self.entries.append(entry)

    def clear(self) -> None:
        """Clear all transcript entries."""

        self.entries.clear()

    def render(
        self,
        *,
        width: int,
    ) -> ChatWorkspaceRenderedTranscript:
        """Render the transcript into styled fragments for the active width."""

        return render_chat_workspace_transcript(self.entries, width=width)

    def render_text(self) -> str:
        """Render the transcript as plain text for a read-only text area."""

        return render_chat_workspace_transcript_text(self.entries)


def render_chat_workspace_transcript(
    entries: tuple[ChatWorkspaceTranscriptEntry, ...] | list[ChatWorkspaceTranscriptEntry],
    *,
    width: int,
) -> ChatWorkspaceRenderedTranscript:
    """Render transcript entries into styled prompt-toolkit fragments."""

    if not entries:
        return ChatWorkspaceRenderedTranscript(plain_text="", fragments=[], line_count=0)

    safe_width = max(_MIN_WRAP_WIDTH, width)
    visual_lines = _build_visual_lines(entries, width=safe_width)

    fragments: StyleAndTextTuples = []
    plain_lines: list[str] = []
    line_count = len(visual_lines)

    for index, rendered_line in enumerate(visual_lines):
        fragments.extend(rendered_line.fragments)
        plain_lines.append(rendered_line.plain_text)
        if index != line_count - 1:
            fragments.append(("", "\n"))

    plain_text = "\n".join(plain_lines)
    return ChatWorkspaceRenderedTranscript(
        plain_text=plain_text,
        fragments=fragments,
        line_count=line_count,
    )


def render_chat_workspace_transcript_text(
    entries: tuple[ChatWorkspaceTranscriptEntry, ...] | list[ChatWorkspaceTranscriptEntry],
) -> str:
    """Render transcript entries as one readable text block."""

    return render_chat_workspace_transcript(entries, width=2_000).plain_text


@dataclass(frozen=True, slots=True)
class _RenderedTranscriptLine:
    """One rendered transcript line with both plain and styled forms."""

    plain_text: str
    fragments: StyleAndTextTuples


def _build_visual_lines(
    entries: tuple[ChatWorkspaceTranscriptEntry, ...] | list[ChatWorkspaceTranscriptEntry],
    *,
    width: int,
) -> tuple[_RenderedTranscriptLine, ...]:
    visual_lines: list[_RenderedTranscriptLine] = []

    for index, entry in enumerate(entries):
        if index > 0 and entry.spacing_before != "tight":
            visual_lines.append(_RenderedTranscriptLine(plain_text="", fragments=[]))
        visual_lines.extend(_render_entry_lines(entry, width=width))

    return tuple(visual_lines)


def _render_entry_lines(
    entry: ChatWorkspaceTranscriptEntry,
    *,
    width: int,
) -> tuple[_RenderedTranscriptLine, ...]:
    if entry.kind == "user":
        return _render_user_lines(entry.text, width=width)
    if entry.kind == "assistant":
        return _render_plain_lines(
            _normalize_lines(entry.text),
            width=width,
            style=_style_for_entry(entry, fallback="class:workspace.assistant"),
        )
    if entry.kind == "plan":
        return _render_plan_lines(entry, width=width)
    if entry.kind in {"notice", "activity", "system"}:
        return _render_bullet_lines(entry, width=width)
    return _render_plain_lines(
        _normalize_lines(entry.text),
        width=width,
        style=_style_for_entry(entry, fallback="class:workspace.assistant"),
    )


def _normalize_lines(text: str) -> tuple[str, ...]:
    stripped_text = text.strip()
    if not stripped_text:
        return ()

    collapsed_lines: list[str] = []
    last_was_blank = False
    for raw_line in stripped_text.splitlines():
        normalized_line = sanitize_terminal_line(raw_line.rstrip())
        if not normalized_line.strip():
            if collapsed_lines and not last_was_blank:
                collapsed_lines.append("")
                last_was_blank = True
            continue
        collapsed_lines.append(normalized_line)
        last_was_blank = False
    return tuple(collapsed_lines)


def _render_user_lines(text: str, *, width: int) -> tuple[_RenderedTranscriptLine, ...]:
    wrapped = _wrap_lines(_normalize_lines(text), width=max(8, width - len(_USER_PREFIX)))
    rendered: list[_RenderedTranscriptLine] = []
    for index, line in enumerate(wrapped):
        prefix = _USER_PREFIX if index == 0 else _CONTINUATION_PREFIX
        content = f"{prefix}{line}".rstrip()
        prefix_fragments: StyleAndTextTuples
        if index == 0:
            prefix_fragments = [
                ("class:workspace.user-label", _USER_LABEL),
                ("class:workspace.user-separator", _USER_SEPARATOR),
            ]
        else:
            prefix_fragments = [("class:workspace.user-separator", prefix)]
        rendered.append(
            _RenderedTranscriptLine(
                plain_text=content,
                fragments=[*prefix_fragments, ("class:workspace.user-text", line)],
            )
        )
    return tuple(rendered)


def _render_plan_lines(
    entry: ChatWorkspaceTranscriptEntry,
    *,
    width: int,
) -> tuple[_RenderedTranscriptLine, ...]:
    title = entry.title or "Proposed Plan"
    rendered: list[_RenderedTranscriptLine] = [
        _RenderedTranscriptLine(
            plain_text=title,
            fragments=[("class:workspace.plan-title", title)],
        )
    ]
    rendered.extend(
        _render_plain_lines(
            _normalize_lines(entry.text),
            width=width,
            style="class:workspace.plan-text",
        )
    )
    return tuple(rendered)


def _render_bullet_lines(
    entry: ChatWorkspaceTranscriptEntry,
    *,
    width: int,
) -> tuple[_RenderedTranscriptLine, ...]:
    style = _style_for_entry(
        entry,
        fallback={
            "notice": "class:workspace.notice",
            "activity": "class:workspace.activity",
            "system": "class:workspace.system",
        }[entry.kind],
    )
    wrapped = _wrap_lines(
        _normalize_lines(entry.text),
        width=max(8, width - len(_ACTIVITY_PREFIX)),
    )
    rendered: list[_RenderedTranscriptLine] = []
    for index, line in enumerate(wrapped):
        prefix = _ACTIVITY_PREFIX if index == 0 else "  "
        content = f"{prefix}{line}".rstrip()
        rendered.append(
            _RenderedTranscriptLine(
                plain_text=content,
                fragments=[(style, content)],
            )
        )
    return tuple(rendered)


def _render_plain_lines(
    lines: tuple[str, ...],
    *,
    width: int,
    style: str,
) -> tuple[_RenderedTranscriptLine, ...]:
    wrapped = _wrap_lines(lines, width=width)
    return tuple(
        _RenderedTranscriptLine(
            plain_text=line,
            fragments=[(style, line)],
        )
        for line in wrapped
    )


def _wrap_lines(lines: tuple[str, ...], *, width: int) -> tuple[str, ...]:
    wrapped_lines: list[str] = []
    effective_width = max(8, width)
    for raw_line in lines:
        segments = textwrap.wrap(
            raw_line,
            width=effective_width,
            break_long_words=True,
            break_on_hyphens=False,
            drop_whitespace=False,
        )
        if not segments:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(segment.rstrip() for segment in segments)
    return tuple(wrapped_lines)


def _style_for_entry(
    entry: ChatWorkspaceTranscriptEntry,
    *,
    fallback: str,
) -> str:
    if entry.accent is None:
        return fallback
    return {
        "thinking": "class:workspace.progress-thinking",
        "planning": "class:workspace.progress-planning",
        "tool": "class:workspace.progress-tool",
        "success": "class:workspace.progress-success",
        "error": "class:workspace.progress-error",
        "detail": "class:workspace.progress-detail",
    }[entry.accent]
