"""Choice-overlay state and rendering helpers for the chat workspace."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class ChoiceOverlayState:
    """Mutable interactive overlay state for confirm/select flows."""

    title: str
    prompt: str
    options: tuple[tuple[str, str], ...]
    future: asyncio.Future[str | None]
    footer_lines: tuple[str, ...]
    selected_index: int = 0


def default_choice_index(
    *,
    options: tuple[tuple[str, str], ...],
    default_value: str | None,
) -> int:
    """Return the selected index for one default option value."""

    if default_value is None:
        return 0
    for index, item in enumerate(options):
        if item[0] == default_value:
            return index
    return 0


def render_choice_overlay_lines(overlay: ChoiceOverlayState) -> tuple[str, ...]:
    """Render one modal choice list into body lines."""

    lines = [overlay.prompt, ""]
    for index, (_value, label) in enumerate(overlay.options):
        prefix = ">" if index == overlay.selected_index else " "
        lines.append(f"{prefix} {label}")
    return tuple(lines)
