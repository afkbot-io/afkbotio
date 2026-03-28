"""Overlay and modal-choice runtime helpers for the chat workspace."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from afkbot.cli.presentation.chat_workspace.choice_overlay import (
    ChoiceOverlayState,
    default_choice_index,
    render_choice_overlay_lines,
)
from afkbot.cli.presentation.chat_workspace.overlays import ChatWorkspaceOverlay


class ChatWorkspaceOverlayRuntime:
    """Own overlay and modal-choice state for one workspace session."""

    def __init__(self, *, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._overlay: ChatWorkspaceOverlay | None = None
        self._choice_overlay: ChoiceOverlayState | None = None

    def current_overlay(self) -> ChatWorkspaceOverlay | None:
        """Return the currently visible overlay, if any."""

        if self._choice_overlay is not None:
            return ChatWorkspaceOverlay(
                title=self._choice_overlay.title,
                body_lines=render_choice_overlay_lines(self._choice_overlay),
                footer_lines=self._choice_overlay.footer_lines,
            )
        return self._overlay

    def overlay_active(self) -> bool:
        """Return whether any overlay currently owns workspace input."""

        return self.current_overlay() is not None

    def choice_overlay_active(self) -> bool:
        """Return whether a modal choice overlay is currently visible."""

        return self._choice_overlay is not None

    def set_overlay(self, overlay: ChatWorkspaceOverlay | None) -> None:
        """Set or clear the passive overlay."""

        self._overlay = overlay
        self._on_change()

    def clear_overlay(self) -> None:
        """Clear any active overlay."""

        if self._choice_overlay is not None:
            self._resolve_choice_overlay(None)
            return
        self.set_overlay(None)

    def dismiss_overlay(self) -> bool:
        """Dismiss the current overlay when one is visible."""

        if self.current_overlay() is None:
            return False
        self.clear_overlay()
        return True

    async def choose_option(
        self,
        *,
        title: str,
        prompt: str,
        options: tuple[tuple[str, str], ...],
        default_value: str | None = None,
        footer_lines: tuple[str, ...] = (),
    ) -> str | None:
        """Render one choice overlay and wait for the selected value."""

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        self._choice_overlay = ChoiceOverlayState(
            title=title,
            prompt=prompt,
            options=options,
            future=future,
            footer_lines=footer_lines,
            selected_index=default_choice_index(
                options=options,
                default_value=default_value,
            ),
        )
        self._on_change()
        try:
            return await future
        finally:
            if self._choice_overlay is not None and self._choice_overlay.future is future:
                self._choice_overlay = None
                self._on_change()

    async def confirm(
        self,
        *,
        title: str,
        question: str,
        default: bool,
        yes_label: str,
        no_label: str,
        hint_text: str | None = None,
        cancel_result: bool | None = None,
    ) -> bool:
        """Show one yes/no confirmation overlay."""

        selected = await self.choose_option(
            title=title,
            prompt=question,
            options=(("yes", yes_label), ("no", no_label)),
            default_value="yes" if default else "no",
            footer_lines=tuple(filter(None, (hint_text, "Enter choose · Esc cancel"))),
        )
        if selected is None:
            if cancel_result is not None:
                return cancel_result
            return default
        return selected == "yes"

    def accept_current_choice(self) -> None:
        """Accept the currently highlighted choice."""

        overlay = self._choice_overlay
        if overlay is None:
            return
        if not overlay.options:
            self._resolve_choice_overlay(None)
            return
        value = overlay.options[overlay.selected_index][0]
        self._resolve_choice_overlay(value)

    def next_choice(self) -> bool:
        """Move the modal selection forward."""

        return self._move_choice(step=1)

    def previous_choice(self) -> bool:
        """Move the modal selection backward."""

        return self._move_choice(step=-1)

    def _move_choice(self, *, step: int) -> bool:
        overlay = self._choice_overlay
        if overlay is None or not overlay.options:
            return False
        overlay.selected_index = (overlay.selected_index + step) % len(overlay.options)
        self._on_change()
        return True

    def _resolve_choice_overlay(self, value: str | None) -> None:
        overlay = self._choice_overlay
        if overlay is None:
            return
        if not overlay.future.done():
            overlay.future.set_result(value)
        self._choice_overlay = None
        self._on_change()
