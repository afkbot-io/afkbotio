"""Tests for chat workspace surface-state helpers."""

from __future__ import annotations

from afkbot.cli.presentation.chat_workspace.layout import (
    ChatWorkspaceSurfaceState,
    render_chat_workspace_surface_text,
)


def test_render_chat_workspace_surface_text_uses_empty_fallback() -> None:
    """Empty surface text should render the provided fallback."""

    rendered = render_chat_workspace_surface_text((), empty_text="No status yet.")

    assert rendered == "No status yet."


def test_render_chat_workspace_surface_text_joins_multiple_lines() -> None:
    """Surface helper should join compact line tuples with newlines."""

    rendered = render_chat_workspace_surface_text(("Working", "Queued 1"))

    assert rendered == "Working\nQueued 1"


def test_chat_workspace_surface_state_defaults_to_empty_lines() -> None:
    """Surface-state dataclass should default to empty status and queue lines."""

    state = ChatWorkspaceSurfaceState()

    assert state.status_lines == ()
    assert state.queue_lines == ()
