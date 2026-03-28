"""Tests for fullscreen chat workspace layout helpers."""

from __future__ import annotations

from prompt_toolkit.widgets import TextArea

from afkbot.cli.presentation.chat_workspace.layout import (
    build_chat_workspace_layout,
    build_chat_workspace_root_container,
    render_chat_workspace_surface_text,
)
from afkbot.cli.presentation.chat_workspace.overlays import (
    ChatWorkspaceOverlay,
    build_chat_workspace_overlay_container,
    render_chat_workspace_overlay_text,
)


def test_render_chat_workspace_surface_text_uses_empty_fallback() -> None:
    """Empty surface text should render the provided fallback."""

    # Arrange
    lines: tuple[str, ...] = ()

    # Act
    rendered = render_chat_workspace_surface_text(lines, empty_text="No status yet.")

    # Assert
    assert rendered == "No status yet."


def test_render_chat_workspace_overlay_text_includes_footer_block() -> None:
    """Overlay text should keep footer hints separated from the body."""

    # Arrange
    overlay = ChatWorkspaceOverlay(
        title="Execution",
        body_lines=("Execute the task using this plan?",),
        footer_lines=("Esc close", "Enter run"),
    )

    # Act
    rendered = render_chat_workspace_overlay_text(overlay)

    # Assert
    assert rendered == (
        "Execution\n"
        "Execute the task using this plan?\n\n"
        "Esc close\n"
        "Enter run"
    )


def test_build_chat_workspace_layout_focuses_the_composer() -> None:
    """The fullscreen layout should focus the composer container by default."""

    # Arrange
    transcript = TextArea(text="Transcript", read_only=True)
    status = TextArea(text="Working", read_only=True)
    queue = TextArea(text="Queued", read_only=True)
    composer = TextArea(text="", multiline=False)
    footer = TextArea(text="Hints", read_only=True)
    root = build_chat_workspace_root_container(
        transcript_compact_container=transcript,
        transcript_docked_container=TextArea(text="", read_only=True),
        transcript_gap_container=TextArea(text="", read_only=True),
        status_container=status,
        queue_container=queue,
        composer_container=composer,
        footer_container=footer,
    )
    overlay_root = build_chat_workspace_overlay_container(
        body=root,
        overlay_getter=lambda: None,
        inline_completion_visible=lambda: False,
    )

    # Act
    layout = build_chat_workspace_layout(
        root_container=overlay_root,
        focused_element=composer.window,
    )

    # Assert
    assert layout.has_focus(composer.window)
