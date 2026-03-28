"""Layout builders for the Codex-like fullscreen chat workspace."""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.layout import AnyContainer, HSplit, Layout
from prompt_toolkit.layout.layout import FocusableElement


@dataclass(frozen=True, slots=True)
class ChatWorkspaceSurfaceState:
    """Text surfaces rendered between transcript and footer."""

    status_lines: tuple[str, ...] = ()
    queue_lines: tuple[str, ...] = ()


def render_chat_workspace_surface_text(
    lines: tuple[str, ...],
    *,
    empty_text: str = "",
) -> str:
    """Render one compact workspace surface into plain text."""

    if not lines:
        return empty_text
    return "\n".join(lines)


def build_chat_workspace_root_container(
    *,
    transcript_compact_container: AnyContainer,
    transcript_docked_container: AnyContainer,
    transcript_gap_container: AnyContainer,
    status_container: AnyContainer,
    queue_container: AnyContainer,
    composer_container: AnyContainer,
    footer_container: AnyContainer,
) -> HSplit:
    """Build the fullscreen workspace container tree."""

    return HSplit(
        [
            transcript_compact_container,
            transcript_docked_container,
            transcript_gap_container,
            status_container,
            queue_container,
            composer_container,
            footer_container,
        ],
        padding=0,
    )


def build_chat_workspace_layout(
    *,
    root_container: AnyContainer,
    focused_element: FocusableElement | None,
) -> Layout:
    """Build one prompt-toolkit layout for the fullscreen workspace."""

    return Layout(container=root_container, focused_element=focused_element)
