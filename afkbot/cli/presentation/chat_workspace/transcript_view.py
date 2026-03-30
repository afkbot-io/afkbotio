"""Transcript viewport state and docking helpers for the chat workspace."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from afkbot.cli.presentation.chat_workspace.transcript import (
    ChatWorkspaceRenderedTranscript,
    ChatWorkspaceTranscript,
)

_MIN_TRANSCRIPT_WIDTH = 24


class ChatWorkspaceTranscriptView:
    """Own transcript rendering, docking, and tail-follow state."""

    def __init__(
        self,
        *,
        transcript: ChatWorkspaceTranscript,
        terminal_size_getter: Callable[[], tuple[int, int]],
    ) -> None:
        self._transcript = transcript
        self._terminal_size_getter = terminal_size_getter
        self._plain_text = ""
        self._fragments: StyleAndTextTuples = []
        self._line_count = 0
        self._docked = False

        self.compact_window = Window(
            content=FormattedTextControl(lambda: self._fragments),
            wrap_lines=True,
            dont_extend_height=True,
            always_hide_cursor=True,
            style="class:workspace.transcript",
        )
        self.docked_window = Window(
            content=FormattedTextControl(lambda: self._fragments),
            wrap_lines=True,
            always_hide_cursor=True,
            style="class:workspace.transcript",
        )

    @property
    def plain_text(self) -> str:
        """Return the last rendered plain-text transcript."""

        return self._plain_text

    @property
    def line_count(self) -> int:
        """Return the rendered visual line count."""

        return self._line_count

    @property
    def docked(self) -> bool:
        """Return whether the transcript currently fills the remaining viewport."""

        return self._docked

    def has_content(self) -> bool:
        """Return whether the transcript currently has visible lines."""

        return self._line_count > 0

    def refresh(self) -> None:
        """Refresh the rendered transcript against the current terminal size."""

        self.refresh_with_surfaces(
            has_status_text=False,
            has_queue_text=False,
        )

    def refresh_with_surfaces(
        self,
        *,
        has_status_text: bool,
        has_queue_text: bool,
    ) -> None:
        """Refresh the rendered transcript using the current stacked surface rows."""

        full_rendered = self._transcript.render(width=self._content_width(docked=False))
        visible_height = self.visible_height(
            has_transcript_content=full_rendered.line_count > 0,
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )
        dock_transcript = self._should_dock(
            full_rendered.line_count,
            has_transcript_content=full_rendered.line_count > 0,
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )
        rendered = full_rendered
        if dock_transcript:
            rendered = self._transcript.render(
                width=self._content_width(docked=True),
                max_lines=visible_height,
            )
        self._apply(
            rendered,
            docked=dock_transcript,
        )

    def vertical_scroll(self, window: Window) -> int:
        """Docked transcripts render only the visible tail and no longer scroll."""

        _ = window
        return 0

    def visible_height(
        self,
        *,
        has_transcript_content: bool | None = None,
        has_status_text: bool,
        has_queue_text: bool,
    ) -> int:
        """Return the available transcript height after fixed UI rows."""

        _columns, rows = self._terminal_size_getter()
        reserved_rows = 4
        if self.has_content() if has_transcript_content is None else has_transcript_content:
            reserved_rows += 1
        if has_status_text:
            reserved_rows += 1
        if has_queue_text:
            reserved_rows += 1
        return max(1, rows - reserved_rows)

    def _apply(
        self,
        rendered: ChatWorkspaceRenderedTranscript,
        *,
        docked: bool,
    ) -> None:
        self._plain_text = rendered.plain_text
        self._fragments = rendered.fragments
        self._line_count = rendered.line_count
        self._docked = docked

    def _content_width(self, *, docked: bool) -> int:
        columns, _rows = self._terminal_size_getter()
        _ = docked
        return max(_MIN_TRANSCRIPT_WIDTH, columns)

    def _should_dock(
        self,
        line_count: int,
        *,
        has_transcript_content: bool,
        has_status_text: bool,
        has_queue_text: bool,
    ) -> bool:
        if line_count <= 0:
            return False
        return line_count > self.visible_height(
            has_transcript_content=has_transcript_content,
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )
