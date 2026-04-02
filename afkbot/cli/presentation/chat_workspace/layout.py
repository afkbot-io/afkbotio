"""Shared chat workspace surface state helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatWorkspaceSurfaceState:
    """Text surfaces rendered around the bottom prompt."""

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
