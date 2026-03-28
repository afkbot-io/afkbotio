"""Application and layout assembly for the fullscreen chat workspace."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindingsBase
from prompt_toolkit.layout import ConditionalContainer, Window
from prompt_toolkit.widgets import Box, TextArea

from afkbot.cli.presentation.chat_workspace.overlays import (
    ChatWorkspaceOverlay,
    build_chat_workspace_overlay_container,
)
from afkbot.cli.presentation.chat_workspace.layout import (
    build_chat_workspace_layout,
    build_chat_workspace_root_container,
)
from afkbot.cli.presentation.chat_workspace.surface_runtime import (
    ChatWorkspaceSurfaceRuntime,
)
from afkbot.cli.presentation.chat_workspace.theme import build_chat_workspace_style


def build_chat_workspace_application(
    *,
    surface_runtime: ChatWorkspaceSurfaceRuntime,
    composer_area: TextArea,
    overlay_getter: Callable[[], ChatWorkspaceOverlay | None],
    inline_completion_visible: Callable[[], bool],
    key_bindings: KeyBindingsBase,
) -> Application[None]:
    """Assemble one prompt-toolkit fullscreen application for chat workspace UX."""

    root_container = build_chat_workspace_root_container(
        transcript_compact_container=ConditionalContainer(
            content=surface_runtime.transcript_compact_window,
            filter=Condition(surface_runtime.show_compact_transcript),
        ),
        transcript_docked_container=ConditionalContainer(
            content=surface_runtime.transcript_docked_window,
            filter=Condition(surface_runtime.show_docked_transcript),
        ),
        transcript_gap_container=ConditionalContainer(
            content=_build_spacer_window(style="class:workspace.transcript"),
            filter=Condition(surface_runtime.has_transcript_content),
        ),
        status_container=ConditionalContainer(
            content=surface_runtime.status_window,
            filter=Condition(surface_runtime.has_status_text),
        ),
        queue_container=ConditionalContainer(
            content=surface_runtime.queue_window,
            filter=Condition(surface_runtime.has_queue_text),
        ),
        composer_container=Box(
            body=composer_area,
            padding_left=1,
            padding_right=1,
            padding_top=1,
            padding_bottom=1,
            style="class:workspace.composer-shell",
            height=3,
        ),
        footer_container=Box(
            body=surface_runtime.footer_window,
            padding_left=1,
            padding_right=1,
            style="class:workspace.footer-shell",
            height=1,
        ),
    )
    float_root = build_chat_workspace_overlay_container(
        body=root_container,
        overlay_getter=overlay_getter,
        inline_completion_visible=inline_completion_visible,
    )
    layout = build_chat_workspace_layout(
        root_container=float_root,
        focused_element=composer_area.window,
    )
    return Application(
        layout=layout,
        key_bindings=key_bindings,
        full_screen=True,
        mouse_support=False,
        style=build_chat_workspace_style(),
    )


def _build_spacer_window(*, style: str) -> Window:
    return Window(height=1, char=" ", style=style)
