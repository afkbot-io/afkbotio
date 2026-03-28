"""Key bindings for the fullscreen chat workspace."""

# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent


@dataclass(frozen=True, slots=True)
class ChatWorkspaceKeyHandlers:
    """Callback bundle used by the fullscreen workspace key bindings."""

    submit_draft: Callable[[], object | None]
    interrupt: Callable[[], None]
    dismiss_context: Callable[[], bool] | None = None
    choice_overlay_active: Callable[[], bool] | None = None
    completion_active: Callable[[], bool] | None = None
    next_choice: Callable[[], bool] | None = None
    previous_choice: Callable[[], bool] | None = None
    next_completion: Callable[[], bool] | None = None
    previous_completion: Callable[[], bool] | None = None
    escape_interrupts: Callable[[], bool] | None = None


def build_chat_workspace_key_bindings(
    handlers: ChatWorkspaceKeyHandlers,
    ) -> KeyBindings:
    """Build the canonical fullscreen workspace key bindings."""

    bindings = KeyBindings()
    choice_overlay_active = Condition(
        lambda: False
        if handlers.choice_overlay_active is None
        else handlers.choice_overlay_active()
    )
    completion_active = Condition(
        lambda: False
        if handlers.completion_active is None
        else handlers.completion_active()
    )

    @bindings.add("enter")
    def _submit(_event: KeyPressEvent) -> None:
        handlers.submit_draft()

    @bindings.add("c-c")
    def _interrupt(_event: KeyPressEvent) -> None:
        handlers.interrupt()

    @bindings.add("tab")
    def _tab(event: KeyPressEvent) -> None:
        if handlers.next_completion is not None and handlers.next_completion():
            return
        getattr(event, "app").layout.focus_next()

    @bindings.add("s-tab")
    def _backtab(event: KeyPressEvent) -> None:
        if handlers.previous_completion is not None and handlers.previous_completion():
            return
        getattr(event, "app").layout.focus_previous()

    @bindings.add("up", filter=choice_overlay_active)
    def _up(_event: KeyPressEvent) -> None:
        if handlers.previous_choice is not None:
            handlers.previous_choice()

    @bindings.add("down", filter=choice_overlay_active)
    def _down(_event: KeyPressEvent) -> None:
        if handlers.next_choice is not None:
            handlers.next_choice()

    @bindings.add("up", filter=completion_active)
    def _completion_up(_event: KeyPressEvent) -> None:
        if handlers.previous_completion is not None:
            handlers.previous_completion()

    @bindings.add("down", filter=completion_active)
    def _completion_down(_event: KeyPressEvent) -> None:
        if handlers.next_completion is not None:
            handlers.next_completion()

    @bindings.add("escape")
    def _dismiss_context(_event: KeyPressEvent) -> None:
        handled = False
        if handlers.dismiss_context is not None:
            handled = bool(handlers.dismiss_context())
        if handled:
            return
        if handlers.escape_interrupts is not None and handlers.escape_interrupts():
            handlers.interrupt()

    return bindings
