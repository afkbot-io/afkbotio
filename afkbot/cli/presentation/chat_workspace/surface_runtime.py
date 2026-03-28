"""Transcript and stacked surface state for the fullscreen chat workspace."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from afkbot.cli.presentation.chat_workspace.layout import (
    ChatWorkspaceSurfaceState,
    render_chat_workspace_surface_text,
)
from afkbot.cli.presentation.chat_workspace.toolbar import DEFAULT_CHAT_WORKSPACE_FOOTER
from afkbot.cli.presentation.chat_workspace.transcript import (
    ChatWorkspaceTranscript,
    ChatWorkspaceTranscriptEntry,
)
from afkbot.cli.presentation.chat_workspace.transcript_view import (
    ChatWorkspaceTranscriptView,
)


class ChatWorkspaceSurfaceRuntime:
    """Own transcript rendering plus status, queue, and footer surface text."""

    def __init__(
        self,
        *,
        transcript: ChatWorkspaceTranscript | None = None,
        surface_state: ChatWorkspaceSurfaceState | None = None,
        terminal_size_getter: Callable[[], tuple[int, int]],
        on_invalidate: Callable[[], None],
    ) -> None:
        self._transcript = transcript or ChatWorkspaceTranscript()
        self._surface_state = surface_state or ChatWorkspaceSurfaceState()
        self._on_invalidate = on_invalidate
        self._footer_text = DEFAULT_CHAT_WORKSPACE_FOOTER
        self._status_text = ""
        self._queue_text = ""
        self._transcript_view = ChatWorkspaceTranscriptView(
            transcript=self._transcript,
            terminal_size_getter=terminal_size_getter,
        )
        self.transcript_compact_window = self._transcript_view.compact_window
        self.transcript_docked_window = self._transcript_view.docked_window
        self.status_window = _build_static_window(
            lambda: self._status_text,
            style="class:workspace.status-line",
        )
        self.queue_window = _build_static_window(
            lambda: self._queue_text,
            style="class:workspace.queue-line",
        )
        self.footer_window = _build_static_window(
            lambda: self._footer_text,
            style="class:workspace.footer-line",
        )

    @property
    def transcript_text(self) -> str:
        """Return the last rendered plain-text transcript."""

        return self._transcript_view.plain_text

    @property
    def status_text(self) -> str:
        """Return the currently rendered status surface text."""

        return self._status_text

    @property
    def queue_text(self) -> str:
        """Return the currently rendered queue surface text."""

        return self._queue_text

    @property
    def footer_text(self) -> str:
        """Return the current footer/help text."""

        return self._footer_text or ""

    def append_transcript_entry(self, entry: ChatWorkspaceTranscriptEntry) -> None:
        """Append one transcript entry and refresh the visible transcript."""

        self._transcript.append(entry)
        self.refresh()

    def replace_surface_state(self, surface_state: ChatWorkspaceSurfaceState) -> None:
        """Replace the stacked workspace surface state and refresh the UI."""

        self._surface_state = surface_state
        self.refresh()

    def set_toolbar_text(self, text: str) -> None:
        """Replace the current footer text and refresh the workspace."""

        self._footer_text = text
        self.refresh()

    def refresh(self) -> None:
        """Refresh all visible workspace text surfaces."""

        self._status_text = render_chat_workspace_surface_text(self._surface_state.status_lines)
        self._queue_text = render_chat_workspace_surface_text(self._surface_state.queue_lines)
        self._transcript_view.refresh_with_surfaces(
            has_status_text=self.has_status_text(),
            has_queue_text=self.has_queue_text(),
        )
        self._on_invalidate()

    def transcript_docked(self) -> bool:
        """Return whether the transcript currently uses the docked viewport layout."""

        return self._transcript_view.docked

    def has_status_text(self) -> bool:
        """Return whether one non-empty status surface is visible."""

        return bool(self._status_text.strip())

    def has_queue_text(self) -> bool:
        """Return whether one non-empty queue surface is visible."""

        return bool(self._queue_text.strip())

    def has_transcript_content(self) -> bool:
        """Return whether the transcript currently has visible content."""

        return self._transcript_view.has_content()

    def show_compact_transcript(self) -> bool:
        """Return whether the transcript should stay attached above the composer."""

        return self._transcript_view.has_content() and not self._transcript_view.docked

    def show_docked_transcript(self) -> bool:
        """Return whether the transcript should fill the docked viewport."""

        return self._transcript_view.has_content() and self._transcript_view.docked

    def docked_transcript_vertical_scroll(self, window: Window) -> int:
        """Keep the docked transcript anchored to the newest visible lines."""

        return self._transcript_view.vertical_scroll(window)

    def transcript_visible_height(self) -> int:
        """Return the visible transcript height with fixed surfaces removed."""

        return self._transcript_view.visible_height(
            has_status_text=self.has_status_text(),
            has_queue_text=self.has_queue_text(),
        )


def _build_static_window(
    text_getter: Callable[[], str],
    *,
    style: str,
) -> Window:
    return Window(
        content=FormattedTextControl(text_getter),
        wrap_lines=True,
        dont_extend_height=True,
        always_hide_cursor=True,
        style=style,
    )
