"""Prompt-toolkit application lifecycle helpers for the chat workspace."""

from __future__ import annotations

from typing import cast

from prompt_toolkit.application import Application


class ChatWorkspaceApplicationRuntime:
    """Own prompt-toolkit application access, focus checks, and terminal queries."""

    def __init__(self) -> None:
        self._application: Application[None] | None = None
        self._exit_requested = False

    @property
    def application(self) -> Application[None]:
        """Return the bound prompt-toolkit application."""

        return cast(Application[None], self._application)

    @property
    def exit_requested(self) -> bool:
        """Return whether the workspace requested shutdown."""

        return self._exit_requested

    def current(self) -> Application[None] | None:
        """Return the current prompt-toolkit application when bound."""

        return self._application

    def bind(self, application: Application[None]) -> None:
        """Attach one prompt-toolkit application instance to the runtime."""

        self._application = application

    def clear_scrollback(self) -> None:
        """Best-effort clear terminal scrollback before fullscreen workspace handoff."""

        application = self._application
        if application is None:
            return
        output = getattr(application, "output", None)
        write_raw = getattr(output, "write_raw", None)
        flush = getattr(output, "flush", None)
        if not callable(write_raw):
            return
        try:
            write_raw("\x1b[3J\x1b[H\x1b[2J")
            if callable(flush):
                flush()
        except RuntimeError:
            return

    def request_exit(self) -> None:
        """Mark the workspace for exit and stop the application when active."""

        self._exit_requested = True
        if self._application is not None and self._application.is_running:
            self._application.exit()

    def invalidate(self) -> None:
        """Best-effort invalidate call for prompt-toolkit redraws."""

        invalidate = getattr(self._application, "invalidate", None)
        if not callable(invalidate):
            return
        try:
            invalidate()
        except RuntimeError:
            return

    def has_focus(self, target: object) -> bool:
        """Return whether one prompt-toolkit target currently owns focus."""

        application = self._application
        if application is None:
            return False
        has_focus = getattr(application.layout, "has_focus", None)
        if not callable(has_focus):
            return False
        try:
            return bool(has_focus(target))
        except RuntimeError:
            return False

    def terminal_size(self) -> tuple[int, int]:
        """Return the current terminal size with stable minimum bounds."""

        output = getattr(self._application, "output", None)
        get_size = getattr(output, "get_size", None)
        if not callable(get_size):
            return 80, 24
        try:
            size = get_size()
        except RuntimeError:
            return 80, 24
        columns = int(getattr(size, "columns", 80) or 80)
        rows = int(getattr(size, "rows", 24) or 24)
        return max(24, columns), max(8, rows)
