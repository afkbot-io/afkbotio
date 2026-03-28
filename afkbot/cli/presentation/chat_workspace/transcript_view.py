"""Transcript viewport state and docking helpers for the chat workspace."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout import Window

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
        self._has_status_text = False
        self._has_queue_text = False

        self.compact_window = Window(
            content=FormattedTextControl(lambda: self._fragments),
            wrap_lines=True,
            dont_extend_height=True,
            always_hide_cursor=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            style="class:workspace.transcript",
        )
        self.docked_window = Window(
            content=FormattedTextControl(lambda: self._fragments),
            wrap_lines=True,
            always_hide_cursor=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            get_vertical_scroll=self.vertical_scroll,
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

        rendered = self._transcript.render(width=self._content_width(docked=False))
        dock_transcript = self._should_dock(
            rendered.line_count,
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )
        if dock_transcript:
            rendered = self._transcript.render(width=self._content_width(docked=True))
        self._has_status_text = has_status_text
        self._has_queue_text = has_queue_text
        self._apply(
            rendered,
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )

    def vertical_scroll(self, window: Window) -> int:
        """Keep the docked transcript anchored to the newest visible lines."""

        visible_height = self._window_height(window)
        if visible_height <= 0:
            return 0
        return max(0, self._line_count - visible_height)

    def visible_height(
        self,
        *,
        has_status_text: bool,
        has_queue_text: bool,
    ) -> int:
        """Return the available transcript height after fixed UI rows."""

        _columns, rows = self._terminal_size_getter()
        reserved_rows = 4
        if self.has_content():
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
        has_status_text: bool,
        has_queue_text: bool,
    ) -> None:
        self._plain_text = rendered.plain_text
        self._fragments = rendered.fragments
        self._line_count = rendered.line_count
        self._docked = self._should_dock(
            rendered.line_count,
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )

    def _content_width(self, *, docked: bool) -> int:
        columns, _rows = self._terminal_size_getter()
        scrollbar_width = 1 if docked else 0
        return max(_MIN_TRANSCRIPT_WIDTH, columns - scrollbar_width)

    def _should_dock(
        self,
        line_count: int,
        *,
        has_status_text: bool,
        has_queue_text: bool,
    ) -> bool:
        if line_count <= 0:
            return False
        return line_count > self.visible_height(
            has_status_text=has_status_text,
            has_queue_text=has_queue_text,
        )

    def _window_height(self, window: Window) -> int:
        render_info = getattr(window, "render_info", None)
        window_height = getattr(render_info, "window_height", 0) if render_info is not None else 0
        if isinstance(window_height, int) and window_height > 0:
            return window_height
        return self.visible_height(
            has_status_text=self._has_status_text,
            has_queue_text=self._has_queue_text,
        )
