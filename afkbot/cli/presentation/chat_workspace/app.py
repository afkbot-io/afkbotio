"""Fullscreen prompt-toolkit workspace foundation for interactive chat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
from prompt_toolkit.layout import Window

from afkbot.cli.presentation.chat_workspace.application_runtime import (
    ChatWorkspaceApplicationRuntime,
)
from afkbot.cli.presentation.chat_workspace.composer_runtime import (
    ChatWorkspaceComposerRuntime,
)
from afkbot.cli.presentation.chat_workspace.keymap import (
    ChatWorkspaceKeyHandlers,
    build_chat_workspace_key_bindings,
)
from afkbot.cli.presentation.chat_workspace.layout import ChatWorkspaceSurfaceState
from afkbot.cli.presentation.chat_workspace.overlay_runtime import (
    ChatWorkspaceOverlayRuntime,
)
from afkbot.cli.presentation.chat_workspace.overlays import ChatWorkspaceOverlay
from afkbot.cli.presentation.chat_workspace.shell_runtime import (
    build_chat_workspace_application,
)
from afkbot.cli.presentation.chat_workspace.surface_runtime import (
    ChatWorkspaceSurfaceRuntime,
)
from afkbot.cli.presentation.chat_workspace.transcript import ChatWorkspaceTranscriptEntry


@dataclass(frozen=True, slots=True)
class ChatWorkspaceSnapshot:
    """Inspectable snapshot of the fullscreen workspace text surfaces."""

    transcript_text: str
    status_text: str
    queue_text: str
    footer_text: str
    overlay_title: str | None
    draft_text: str


class ChatWorkspaceApp:
    """Own one fullscreen workspace shell for interactive chat REPL sessions."""

    def __init__(
        self,
        *,
        title: str = "AFK Chat Workspace",
        surface_state: ChatWorkspaceSurfaceState | None = None,
        composer_completer: Completer | None = None,
        interrupt: Callable[[], None] | None = None,
    ) -> None:
        self._title = title
        self._application_runtime = ChatWorkspaceApplicationRuntime()
        self._interrupt = interrupt or self.request_exit
        self._overlay_runtime = ChatWorkspaceOverlayRuntime(on_change=self._invalidate)
        self._surface_runtime = ChatWorkspaceSurfaceRuntime(
            surface_state=surface_state,
            terminal_size_getter=lambda: self._terminal_size(),
            on_invalidate=self._invalidate,
        )
        self._transcript_docked_window = self._surface_runtime.transcript_docked_window
        self._composer_runtime = ChatWorkspaceComposerRuntime(
            completer=composer_completer,
            on_refresh=self.refresh,
            on_invalidate=self._invalidate,
            choice_overlay_active=self.choice_overlay_active,
            accept_current_choice=self.accept_current_choice,
            has_focus=lambda target: self._has_focus(target),
            application_getter=self._application_runtime.current,
        )
        application = build_chat_workspace_application(
            surface_runtime=self._surface_runtime,
            composer_area=self._composer_runtime.area,
            overlay_getter=self.current_overlay,
            inline_completion_visible=self.completion_visible,
            key_bindings=build_chat_workspace_key_bindings(
                ChatWorkspaceKeyHandlers(
                    submit_draft=self.submit_current_input,
                    interrupt=self._interrupt,
                    dismiss_context=self.dismiss_context,
                    choice_overlay_active=self.choice_overlay_active,
                    completion_active=self.completion_visible,
                    next_choice=self.next_choice,
                    previous_choice=self.previous_choice,
                    next_completion=self.next_completion,
                    previous_completion=self.previous_completion,
                    escape_interrupts=lambda: (
                        not self.overlay_active() and not self.completion_visible()
                    ),
                )
            ),
        )
        self._application_runtime.bind(application)
        self.refresh()

    @property
    def application(self) -> Application[None]:
        """Return the underlying fullscreen prompt-toolkit application."""

        return self._application_runtime.application

    @property
    def title(self) -> str:
        """Return the workspace title."""

        return self._title

    @property
    def exit_requested(self) -> bool:
        """Return whether the workspace requested shutdown."""

        return self._application_runtime.exit_requested

    @property
    def composer_buffer(self) -> Buffer:
        """Expose the composer buffer for focused integration and tests."""

        return self._composer_runtime.buffer

    def current_overlay(self) -> ChatWorkspaceOverlay | None:
        """Return the currently visible workspace overlay, if any."""

        return self._overlay_runtime.current_overlay()

    def overlay_active(self) -> bool:
        """Return whether one interactive overlay currently owns workspace input."""

        return self._overlay_runtime.overlay_active()

    def choice_overlay_active(self) -> bool:
        """Return whether one modal choice overlay currently owns workspace input."""

        return self._overlay_runtime.choice_overlay_active()

    def completion_visible(self) -> bool:
        """Return whether the inline completion popup should be visible."""

        return self._composer_runtime.completion_visible()

    def append_transcript_entry(self, entry: ChatWorkspaceTranscriptEntry) -> None:
        """Append one transcript entry and refresh the visible transcript."""

        self._surface_runtime.append_transcript_entry(entry)

    def replace_surface_state(self, surface_state: ChatWorkspaceSurfaceState) -> None:
        """Replace the stacked workspace surface state and refresh the UI."""

        self._surface_runtime.replace_surface_state(surface_state)

    def set_toolbar_text(self, text: str) -> None:
        """Replace the current footer text and refresh the workspace."""

        self._surface_runtime.set_toolbar_text(text)

    def set_overlay(self, overlay: ChatWorkspaceOverlay | None) -> None:
        """Set or clear the current overlay."""

        self._overlay_runtime.set_overlay(overlay)

    def clear_overlay(self) -> None:
        """Clear the current overlay, if present."""

        self._overlay_runtime.clear_overlay()

    def dismiss_context(self) -> bool:
        """Dismiss the current overlay or active completion menu, if any."""

        if self._overlay_runtime.dismiss_overlay():
            return True
        return self._composer_runtime.dismiss_completion()

    def set_draft_text(self, text: str) -> None:
        """Replace the current composer draft."""

        self._composer_runtime.set_draft_text(text)

    def submit_draft(self) -> str | None:
        """Queue the current composer draft for the runtime session."""

        return self._composer_runtime.submit_draft()

    def submit_current_input(self) -> None:
        """Submit the current overlay choice or composer draft."""

        self._composer_runtime.submit_current_input()

    def pop_next_message(self) -> str | None:
        """Pop the next submitted message, if any."""

        return self._composer_runtime.pop_next_message()

    async def read_submitted_message(self) -> str:
        """Wait for the next submitted message from the fullscreen composer."""

        return await self._composer_runtime.read_submitted_message()

    async def choose_option(
        self,
        *,
        title: str,
        prompt: str,
        options: tuple[tuple[str, str], ...],
        default_value: str | None = None,
        footer_lines: tuple[str, ...] = (),
    ) -> str | None:
        """Render one overlay choice list and wait for a selected value."""

        return await self._overlay_runtime.choose_option(
            title=title,
            prompt=prompt,
            options=options,
            default_value=default_value,
            footer_lines=footer_lines,
        )

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
        """Show one yes/no confirm overlay and wait for the selected answer."""

        return await self._overlay_runtime.confirm(
            title=title,
            question=question,
            default=default,
            yes_label=yes_label,
            no_label=no_label,
            hint_text=hint_text,
            cancel_result=cancel_result,
        )

    def accept_current_choice(self) -> None:
        """Accept the currently highlighted overlay choice, if one is visible."""

        self._overlay_runtime.accept_current_choice()

    def next_choice(self) -> bool:
        """Move the overlay selection forward when an overlay is active."""

        return self._overlay_runtime.next_choice()

    def previous_choice(self) -> bool:
        """Move the overlay selection backward when an overlay is active."""

        return self._overlay_runtime.previous_choice()

    def next_completion(self) -> bool:
        """Advance the current completion item or trigger completion for the composer."""

        if self.choice_overlay_active():
            return self.next_choice()
        return self._composer_runtime.next_completion()

    def previous_completion(self) -> bool:
        """Move backward in the completion menu when the composer owns focus."""

        if self.choice_overlay_active():
            return self.previous_choice()
        return self._composer_runtime.previous_completion()

    def snapshot(self) -> ChatWorkspaceSnapshot:
        """Capture the current workspace texts for tests and later integration."""

        overlay = self.current_overlay()
        return ChatWorkspaceSnapshot(
            transcript_text=self._surface_runtime.transcript_text,
            status_text=self._surface_runtime.status_text,
            queue_text=self._surface_runtime.queue_text,
            footer_text=self._surface_runtime.footer_text,
            overlay_title=None if overlay is None else overlay.title,
            draft_text=self._composer_runtime.buffer.text,
        )

    def request_exit(self) -> None:
        """Mark the workspace for exit and stop the prompt-toolkit app when active."""

        self._application_runtime.request_exit()

    def refresh(self) -> None:
        """Refresh all visible workspace text surfaces."""

        self._surface_runtime.refresh()

    def clear_scrollback(self) -> None:
        """Best-effort clear terminal scrollback before fullscreen rendering starts."""

        self._application_runtime.clear_scrollback()

    def transcript_docked(self) -> bool:
        """Return whether the transcript currently uses the docked viewport layout."""

        return self._surface_runtime.transcript_docked()

    def _invalidate(self) -> None:
        self._application_runtime.invalidate()

    def _has_focus(self, target: object) -> bool:
        """Return whether the prompt-toolkit layout currently focuses one target element."""

        return self._application_runtime.has_focus(target)

    def _has_status_text(self) -> bool:
        return self._surface_runtime.has_status_text()

    def _has_queue_text(self) -> bool:
        return self._surface_runtime.has_queue_text()

    def _has_transcript_content(self) -> bool:
        return self._surface_runtime.has_transcript_content()

    def _show_compact_transcript(self) -> bool:
        return self._surface_runtime.show_compact_transcript()

    def _show_docked_transcript(self) -> bool:
        return self._surface_runtime.show_docked_transcript()

    def _terminal_size(self) -> tuple[int, int]:
        return self._application_runtime.terminal_size()

    def _docked_transcript_vertical_scroll(self, window: Window) -> int:
        """Keep the docked transcript anchored to the newest visible lines."""

        return self._surface_runtime.docked_transcript_vertical_scroll(window)

    def _transcript_visible_height(self) -> int:
        return self._surface_runtime.transcript_visible_height()
