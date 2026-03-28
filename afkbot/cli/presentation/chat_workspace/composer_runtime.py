"""Composer/input runtime helpers for the fullscreen chat workspace."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import TextArea

from afkbot.cli.presentation.chat_workspace.completion_runtime import (
    apply_completion,
    current_completion,
    should_auto_submit_inline_command,
)


class ChatWorkspaceComposerRuntime:
    """Own composer widget state, queued messages, and completion navigation."""

    def __init__(
        self,
        *,
        completer: Completer | None,
        on_refresh: Callable[[], None],
        on_invalidate: Callable[[], None],
        choice_overlay_active: Callable[[], bool],
        accept_current_choice: Callable[[], None],
        has_focus: Callable[[object], bool],
        application_getter: Callable[[], Application[None] | None],
    ) -> None:
        self._on_refresh = on_refresh
        self._on_invalidate = on_invalidate
        self._choice_overlay_active = choice_overlay_active
        self._accept_current_choice = accept_current_choice
        self._has_focus = has_focus
        self._application_getter = application_getter
        self._submitted_messages: deque[str] = deque()
        self._message_queue: asyncio.Queue[str] | None = None
        self._area = TextArea(
            multiline=False,
            prompt="> ",
            focusable=True,
            wrap_lines=False,
            width=Dimension(weight=1),
            height=1,
            completer=completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=InMemoryHistory(),
            style="class:workspace.composer-field",
        )
        self._area.buffer.read_only = Condition(choice_overlay_active)

    @property
    def area(self) -> TextArea:
        """Return the prompt-toolkit text area used as composer."""

        return self._area

    @property
    def buffer(self) -> Buffer:
        """Expose the underlying composer buffer."""

        return self._area.buffer

    def completion_visible(self) -> bool:
        """Return whether the inline completion popup should be visible."""

        if self._choice_overlay_active():
            return False
        if not self._has_focus(self._area.window):
            return False
        complete_state = self._area.buffer.complete_state
        return complete_state is not None and bool(complete_state.completions)

    def dismiss_completion(self) -> bool:
        """Dismiss the current completion menu, if any."""

        buffer = self._area.buffer
        if buffer.complete_state is None:
            return False
        buffer.cancel_completion()
        self._on_invalidate()
        return True

    def set_draft_text(self, text: str) -> None:
        """Replace the current composer draft text."""

        self._area.buffer.set_document(
            Document(text=text, cursor_position=len(text)),
            bypass_readonly=True,
        )
        self._on_invalidate()

    def submit_draft(self) -> str | None:
        """Queue the current composer draft for consumption by the runtime."""

        message = str(self._area.buffer.text).strip()
        if not message:
            return None
        if self._message_queue is None:
            self._submitted_messages.append(message)
        else:
            self._message_queue.put_nowait(message)
        self._area.buffer.text = ""
        self._on_refresh()
        return message

    def submit_current_input(self) -> None:
        """Submit the current choice or composer draft."""

        if self._choice_overlay_active():
            self._accept_current_choice()
            return

        buffer = self._area.buffer
        completion = current_completion(buffer.complete_state)
        if completion is not None:
            auto_submit = should_auto_submit_inline_command(
                draft=buffer.text,
                completion=completion,
            )
            apply_completion(buffer, completion)
            if auto_submit:
                self.submit_draft()
            else:
                self._on_invalidate()
            return

        self.submit_draft()

    def pop_next_message(self) -> str | None:
        """Pop the next submitted message, if any."""

        if not self._submitted_messages:
            return None
        return self._submitted_messages.popleft()

    async def read_submitted_message(self) -> str:
        """Wait for the next submitted message from the composer."""

        next_message = self.pop_next_message()
        if next_message is not None:
            return next_message
        if self._message_queue is None:
            self._message_queue = asyncio.Queue()
        return await self._message_queue.get()

    def next_completion(self) -> bool:
        """Advance the current completion item or trigger completion."""

        if self._choice_overlay_active():
            return False
        if not self._has_focus(self._area.window):
            return False
        buffer = self._area.buffer
        application = self._application_getter()
        if buffer.complete_state is None:
            if (
                application is None
                or not application.is_running
                or not self._should_trigger_completion()
            ):
                return False
            buffer.start_completion(select_first=True)
            return True
        buffer.complete_next()
        return True

    def previous_completion(self) -> bool:
        """Move backward in the completion popup."""

        if self._choice_overlay_active():
            return False
        if not self._has_focus(self._area.window):
            return False
        buffer = self._area.buffer
        if buffer.complete_state is None:
            return False
        buffer.complete_previous()
        return True

    def _should_trigger_completion(self) -> bool:
        completer = self._area.completer
        if completer is None:
            return False
        has_completion_context = getattr(completer, "has_completion_context", None)
        if not callable(has_completion_context):
            return False
        return bool(has_completion_context(self._area.buffer.document.text_before_cursor))
