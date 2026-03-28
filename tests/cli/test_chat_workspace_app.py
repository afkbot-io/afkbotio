"""Tests for the fullscreen chat workspace application shell."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import Completion

from afkbot.cli.presentation.chat_workspace.app import ChatWorkspaceApp
from afkbot.cli.presentation.chat_workspace.composer import ChatPromptCompleter
from afkbot.cli.presentation.chat_workspace.layout import ChatWorkspaceSurfaceState
from afkbot.cli.presentation.chat_workspace.overlays import ChatWorkspaceOverlay
from afkbot.cli.presentation.chat_workspace.toolbar import DEFAULT_CHAT_WORKSPACE_FOOTER
from afkbot.cli.presentation.chat_workspace.transcript import ChatWorkspaceTranscriptEntry
from afkbot.services.chat_session.input_catalog import ChatInputCatalog


def test_chat_workspace_app_snapshots_transcript_surface_and_overlay() -> None:
    """The workspace app should mirror transcript, stacked surfaces, and overlay state."""

    # Arrange
    workspace = ChatWorkspaceApp(
        surface_state=ChatWorkspaceSurfaceState(
            status_lines=("• Working... · tool: bash.exec",),
            queue_lines=("◦ Queued 1 message for the next turn.",),
        )
    )
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Ready to help.")
    )
    workspace.set_toolbar_text("/ commands · $ capabilities · @ files")
    workspace.set_overlay(
        ChatWorkspaceOverlay(
            title="Execution",
            body_lines=("Execute the task using this plan?",),
        )
    )

    # Act
    snapshot = workspace.snapshot()

    # Assert
    assert snapshot.transcript_text == "Ready to help."
    assert snapshot.status_text == "• Working... · tool: bash.exec"
    assert snapshot.queue_text == "◦ Queued 1 message for the next turn."
    assert snapshot.footer_text == "/ commands · $ capabilities · @ files"
    assert snapshot.overlay_title == "Execution"


def test_chat_workspace_app_submits_draft_and_records_user_entry() -> None:
    """Submitting the composer draft should queue the message and clear the draft."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace.set_draft_text("run tests")

    # Act
    submitted = workspace.submit_draft()
    next_message = workspace.pop_next_message()
    snapshot = workspace.snapshot()

    # Assert
    assert submitted == "run tests"
    assert next_message == "run tests"
    assert snapshot.draft_text == ""
    assert snapshot.transcript_text == ""


def test_chat_workspace_app_auto_submits_selected_slash_completion() -> None:
    """Submitting `/` with one selected slash completion should apply and queue the command."""

    # Arrange
    workspace = ChatWorkspaceApp(
        composer_completer=ChatPromptCompleter(
            catalog_getter=lambda: ChatInputCatalog(),
            local_commands=("/status", "//status"),
        )
    )
    workspace.set_draft_text("/")
    workspace.composer_buffer.complete_state = CompletionState(
        original_document=workspace.composer_buffer.document,
        completions=[Completion(text="/status", start_position=-1)],
    )

    # Act
    workspace.submit_current_input()
    queued = workspace.pop_next_message()

    # Assert
    assert queued == "/status"
    assert workspace.composer_buffer.text == ""


def test_chat_workspace_app_omits_status_and_queue_rows_without_surface_lines() -> None:
    """Idle workspaces should keep only the transcript, composer, and footer surfaces."""

    # Arrange
    workspace = ChatWorkspaceApp()

    # Act
    snapshot = workspace.snapshot()

    # Assert
    assert snapshot.status_text == ""
    assert snapshot.queue_text == ""
    assert snapshot.footer_text == DEFAULT_CHAT_WORKSPACE_FOOTER


def test_chat_workspace_app_keeps_short_transcripts_in_attached_mode() -> None:
    """Short transcripts should keep the composer visually attached below the transcript."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Ready to help.")
    )

    # Act
    docked = workspace.transcript_docked()

    # Assert
    assert docked is False


def test_chat_workspace_app_docks_long_transcripts_to_fill_the_viewport() -> None:
    """Long transcripts should switch to the docked transcript viewport."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace._terminal_size = lambda: (80, 16)  # type: ignore[method-assign]
    for index in range(18):
        workspace.append_transcript_entry(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=f"Assistant message number {index} with enough text to wrap a little.",
            )
        )

    # Act
    docked = workspace.transcript_docked()

    # Assert
    assert docked is True


def test_chat_workspace_app_tracks_tail_scroll_for_docked_transcripts() -> None:
    """Docked transcripts should auto-follow the newest visible lines."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace._terminal_size = lambda: (80, 12)  # type: ignore[method-assign]
    for index in range(14):
        workspace.append_transcript_entry(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=f"Assistant message number {index}.",
            )
        )

    # Act
    scroll = workspace._docked_transcript_vertical_scroll(  # type: ignore[attr-defined]
        workspace._transcript_docked_window  # type: ignore[attr-defined]
    )

    # Assert
    assert workspace.transcript_docked() is True
    assert scroll == 20


def test_chat_workspace_app_prefers_real_window_height_for_tail_scroll() -> None:
    """Docked tail-follow should use the actual rendered window height when available."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace._terminal_size = lambda: (80, 40)  # type: ignore[method-assign]
    for index in range(14):
        workspace.append_transcript_entry(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=f"Assistant message number {index}.",
            )
        )
    workspace._transcript_docked_window.render_info = SimpleNamespace(window_height=8)  # type: ignore[attr-defined]

    # Act
    scroll = workspace._docked_transcript_vertical_scroll(  # type: ignore[attr-defined]
        workspace._transcript_docked_window  # type: ignore[attr-defined]
    )

    # Assert
    assert scroll == 19


def test_chat_workspace_app_can_request_exit_without_running_application() -> None:
    """Requesting exit should set the local shutdown flag even before app.run()."""

    # Arrange
    workspace = ChatWorkspaceApp()

    # Act
    workspace.request_exit()

    # Assert
    assert workspace.exit_requested is True


def test_chat_workspace_app_completion_helpers_do_not_consume_tab_outside_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion helpers should fall back when the composer does not own focus."""

    # Arrange
    workspace = ChatWorkspaceApp()
    monkeypatch.setattr(workspace, "_has_focus", lambda _target: False)

    # Act
    handled = workspace.next_completion()

    # Assert
    assert handled is False


def test_chat_workspace_app_completion_helpers_navigate_inline_popup() -> None:
    """Arrow-like completion helpers should move selection inside the inline popup."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace.set_draft_text("$")
    workspace.composer_buffer.complete_state = CompletionState(
        original_document=workspace.composer_buffer.document,
        completions=[
            Completion(text="$one", start_position=-1),
            Completion(text="$two", start_position=-1),
        ],
    )

    # Act
    next_handled = workspace.next_completion()
    second_handled = workspace.next_completion()
    previous_handled = workspace.previous_completion()
    current_completion = workspace.composer_buffer.complete_state.current_completion

    # Assert
    assert next_handled is True
    assert second_handled is True
    assert previous_handled is True
    assert current_completion is not None
    assert current_completion.text == "$one"


def test_chat_workspace_app_dismiss_context_clears_completion_state() -> None:
    """Escape handling should close the active completion menu when no overlay is open."""

    # Arrange
    workspace = ChatWorkspaceApp()
    workspace.composer_buffer.complete_state = CompletionState(
        original_document=workspace.composer_buffer.document,
        completions=[Completion(text="//status")],
    )

    # Act
    workspace.dismiss_context()

    # Assert
    assert workspace.composer_buffer.complete_state is None


def test_chat_workspace_app_dismiss_context_reports_when_nothing_closed() -> None:
    """Dismiss-context should return `False` when neither an overlay nor popup is active."""

    # Arrange
    workspace = ChatWorkspaceApp()

    # Act
    handled = workspace.dismiss_context()

    # Assert
    assert handled is False


@pytest.mark.asyncio
async def test_chat_workspace_app_can_resolve_overlay_choice() -> None:
    """Choice overlays should resolve to the currently selected option."""

    # Arrange
    workspace = ChatWorkspaceApp()

    async def _choose() -> str | None:
        return await workspace.choose_option(
            title="Execution",
            prompt="Execute the task using this plan?",
            options=(
                ("yes", "Execute"),
                ("no", "Stop"),
            ),
            default_value="yes",
        )

    task = asyncio.create_task(_choose())

    # Act
    await asyncio.sleep(0)
    workspace.next_choice()
    workspace.accept_current_choice()
    selected = await task

    # Assert
    assert selected == "no"
    assert workspace.snapshot().overlay_title is None


@pytest.mark.asyncio
async def test_chat_workspace_app_choice_overlay_marks_composer_read_only() -> None:
    """Modal choice overlays should freeze composer edits until the overlay closes."""

    # Arrange
    workspace = ChatWorkspaceApp()

    async def _choose() -> str | None:
        return await workspace.choose_option(
            title="Confirm",
            prompt="Proceed?",
            options=(("yes", "Yes"), ("no", "No")),
            default_value="yes",
        )

    task = asyncio.create_task(_choose())

    # Act
    await asyncio.sleep(0)
    read_only_during_overlay = workspace.composer_buffer.read_only()
    workspace.clear_overlay()
    selected = await task
    read_only_after_overlay = workspace.composer_buffer.read_only()

    # Assert
    assert read_only_during_overlay is True
    assert selected is None
    assert read_only_after_overlay is False


@pytest.mark.asyncio
async def test_chat_workspace_app_confirm_can_return_cancel_result() -> None:
    """Escaped confirm overlays should respect the explicit cancel result."""

    # Arrange
    workspace = ChatWorkspaceApp()

    async def _confirm() -> bool:
        return await workspace.confirm(
            title="Execution",
            question="Execute the task using this plan?",
            default=True,
            yes_label="Execute",
            no_label="Stop",
            cancel_result=False,
        )

    task = asyncio.create_task(_confirm())

    # Act
    await asyncio.sleep(0)
    workspace.clear_overlay()
    confirmed = await task

    # Assert
    assert confirmed is False
