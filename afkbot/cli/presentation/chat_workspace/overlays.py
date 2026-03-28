"""Overlay and inline popup primitives for the fullscreen chat workspace."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import AnyContainer, ConditionalContainer, Float, FloatContainer, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.widgets import Box


@dataclass(frozen=True, slots=True)
class ChatWorkspaceOverlay:
    """One modal-like overlay rendered above the fullscreen workspace."""

    title: str
    body_lines: tuple[str, ...]
    footer_lines: tuple[str, ...] = ()


def render_chat_workspace_overlay_text(overlay: ChatWorkspaceOverlay) -> str:
    """Render one overlay into plain text."""

    lines = [overlay.title, *overlay.body_lines]
    if overlay.footer_lines:
        lines.extend(("", *overlay.footer_lines))
    return "\n".join(lines)


def build_chat_workspace_overlay_container(
    *,
    body: AnyContainer,
    overlay_getter: Callable[[], ChatWorkspaceOverlay | None],
    inline_completion_visible: Callable[[], bool],
) -> FloatContainer:
    """Wrap one workspace container with inline completion and modal overlay floats."""

    completion_popup = ConditionalContainer(
        content=CompletionsMenu(max_height=8),
        filter=Condition(inline_completion_visible),
    )
    overlay_window = Window(
        content=FormattedTextControl(
            lambda: render_chat_workspace_overlay_text(overlay_getter() or _EMPTY_OVERLAY)
        ),
        wrap_lines=True,
        always_hide_cursor=True,
        style="class:workspace.overlay.body",
    )
    overlay_container = ConditionalContainer(
        content=Box(
            body=overlay_window,
            padding=1,
            style="class:workspace.overlay",
        ),
        filter=Condition(lambda: overlay_getter() is not None),
    )
    return FloatContainer(
        content=body,
        floats=[
            Float(
                content=completion_popup,
                xcursor=True,
                ycursor=True,
            ),
            Float(
                content=overlay_container,
                top=2,
                bottom=2,
                left=4,
                right=4,
            ),
        ],
    )


_EMPTY_OVERLAY = ChatWorkspaceOverlay(title="", body_lines=())
