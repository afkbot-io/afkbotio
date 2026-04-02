"""Tests for the prompt-session chat workspace application."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import afkbot.cli.presentation.chat_workspace.app as workspace_app_module
from afkbot.cli.presentation.chat_workspace.app import (
    ChatWorkspaceApp,
    ChatWorkspaceChoiceState,
)
from afkbot.cli.presentation.chat_workspace.layout import ChatWorkspaceSurfaceState
from afkbot.cli.presentation.chat_workspace.toolbar import DEFAULT_CHAT_WORKSPACE_FOOTER
from afkbot.cli.presentation.chat_workspace.transcript import ChatWorkspaceTranscriptEntry


def test_chat_workspace_app_snapshots_transcript_and_surface_state() -> None:
    """The workspace snapshot should expose transcript, status, queue, and footer text."""

    workspace = ChatWorkspaceApp(
        surface_state=ChatWorkspaceSurfaceState(
            status_lines=("• Working... · tool: bash.exec",),
            queue_lines=("◦ Queued 1 message for the next turn.",),
        ),
        emit_output=False,
    )
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Ready to help.")
    )
    workspace.set_toolbar_text("/ commands · $ capabilities · @ files")

    snapshot = workspace.snapshot()

    assert snapshot.transcript_text == "Ready to help."
    assert snapshot.status_text == "• Working... · tool: bash.exec"
    assert snapshot.queue_text == "◦ Queued 1 message for the next turn."
    assert snapshot.footer_text == "/ commands · $ capabilities · @ files"
    assert snapshot.overlay_title is None
    assert snapshot.draft_text == ""


def test_chat_workspace_app_keeps_user_entry_in_state_without_echo() -> None:
    """User messages should remain in transcript state even when terminal echo is suppressed."""

    workspace = ChatWorkspaceApp(emit_output=False)

    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="user", text="run tests"),
        echo=False,
    )

    assert workspace.snapshot().transcript_text == "you > run tests"


def test_chat_workspace_app_emits_formatted_terminal_output_without_raw_ansi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal transcript output should use formatted text instead of raw ANSI codes."""

    captured: list[tuple[tuple[tuple[str, str], ...], str]] = []

    def _fake_print_formatted_text(value, *, style, end, flush):  # noqa: ANN001
        _ = style, flush
        captured.append((tuple(value), end))

    monkeypatch.setattr(workspace_app_module, "print_formatted_text", _fake_print_formatted_text)

    workspace = ChatWorkspaceApp(emit_output=True)
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Ready to help."),
    )

    assert captured
    rendered_text = "".join(fragment for _style, fragment in captured[0][0])
    assert "\x1b" not in rendered_text
    assert rendered_text == "Ready to help.\n\n"


def test_chat_workspace_app_collapses_blank_paragraph_rows_in_assistant_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assistant output should avoid extra visual empty rows between paragraphs."""

    captured: list[tuple[tuple[tuple[str, str], ...], str]] = []

    def _fake_print_formatted_text(value, *, style, end, flush):  # noqa: ANN001
        _ = style, flush
        captured.append((tuple(value), end))

    monkeypatch.setattr(workspace_app_module, "print_formatted_text", _fake_print_formatted_text)

    workspace = ChatWorkspaceApp(emit_output=True)
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="assistant", text="First line.\n\nSecond line."),
    )

    assert captured
    rendered_text = "".join(fragment for _style, fragment in captured[0][0])
    assert rendered_text == "First line.\nSecond line.\n\n"


def test_chat_workspace_app_avoids_extra_leading_gap_after_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assistant final text should not add an extra blank row after a user prompt line."""

    captured: list[tuple[tuple[tuple[str, str], ...], str]] = []

    def _fake_print_formatted_text(value, *, style, end, flush):  # noqa: ANN001
        _ = style, flush
        captured.append((tuple(value), end))

    monkeypatch.setattr(workspace_app_module, "print_formatted_text", _fake_print_formatted_text)

    workspace = ChatWorkspaceApp(emit_output=True)
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="user", text="вы"),
        echo=False,
    )
    workspace.append_transcript_entry(
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Да? Что хотел спросить?"),
    )

    assert captured
    rendered_text = "".join(fragment for _style, fragment in captured[0][0])
    assert rendered_text.startswith("Да? Что хотел спросить?")
    assert not rendered_text.startswith("\n")
    assert rendered_text.endswith("\n\n")


def test_chat_workspace_app_omits_status_and_queue_rows_without_surface_lines() -> None:
    """Idle workspaces should keep an empty status/queue state."""

    workspace = ChatWorkspaceApp(emit_output=False)

    snapshot = workspace.snapshot()

    assert snapshot.status_text == ""
    assert snapshot.queue_text == ""
    assert snapshot.footer_text == DEFAULT_CHAT_WORKSPACE_FOOTER


def test_chat_workspace_app_keeps_full_transcript_for_long_histories() -> None:
    """Long transcripts should remain intact instead of clipping to a viewport."""

    workspace = ChatWorkspaceApp(emit_output=False)
    for index in range(14):
        workspace.append_transcript_entry(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=f"Assistant message number {index}.",
            )
        )

    snapshot = workspace.snapshot()

    assert snapshot.transcript_text.startswith("Assistant message number 0.")
    assert snapshot.transcript_text.endswith("Assistant message number 13.")
    assert snapshot.transcript_text.count("Assistant message number") == 14


def test_chat_workspace_prompt_message_includes_status_queue_and_prompt_prefix() -> None:
    """The dynamic prompt message should render stacked status lines above `you >`."""

    workspace = ChatWorkspaceApp(
        surface_state=ChatWorkspaceSurfaceState(
            status_lines=("• Working (3s • esc to interrupt) · thinking...",),
            queue_lines=("◦ Queued 1 message for the next turn.",),
        ),
        emit_output=False,
    )

    fragments = workspace._prompt_message()  # type: ignore[attr-defined]

    assert fragments == [
        ("class:workspace.status-line", "• Working (3s • esc to interrupt) · thinking..."),
        ("", "\n"),
        ("class:workspace.queue-line", "◦ Queued 1 message for the next turn."),
        ("", "\n"),
        ("class:workspace.user-label", "you"),
        ("class:workspace.user-separator", " > "),
    ]


def test_chat_workspace_bottom_toolbar_uses_current_footer_text() -> None:
    """The bottom toolbar should mirror the current footer/help text."""

    workspace = ChatWorkspaceApp(emit_output=False)
    workspace.set_toolbar_text("/ commands · $ capabilities · @ files · plan=on")

    assert workspace._bottom_toolbar() == [  # type: ignore[attr-defined]
        ("class:workspace.footer-line", " / commands · $ capabilities · @ files · plan=on")
    ]


def test_chat_workspace_choice_mode_renders_inside_workspace_prompt() -> None:
    """Choice prompts should reuse the workspace prompt and toolbar surfaces."""

    workspace = ChatWorkspaceApp(
        surface_state=ChatWorkspaceSurfaceState(
            status_lines=("• Working (3s • esc to interrupt) · thinking...",),
        ),
        emit_output=False,
    )
    workspace._choice_state = ChatWorkspaceChoiceState(  # type: ignore[attr-defined]
        title="Tool access request",
        prompt="Approve access to tool: bash.exec?",
        options=(
            ("allow_once", "Run once"),
            ("allow_session", "Allow for session"),
            ("deny", "Do not run"),
        ),
        default_index=2,
        selected_index=1,
        footer_lines=("↑/↓ move, Enter confirm, Esc cancel",),
    )

    fragments = workspace._prompt_message()  # type: ignore[attr-defined]

    assert fragments == [
        ("class:workspace.status-line", "• Working (3s • esc to interrupt) · thinking..."),
        ("", "\n"),
        ("class:workspace.plan-title", "Tool access request"),
        ("", "\n"),
        ("class:workspace.assistant", "Approve access to tool: bash.exec?"),
        ("", "\n\n"),
        ("class:workspace.notice", "  ( ) Run once\n"),
        ("class:workspace.thinking", "> (*) Allow for session\n"),
        ("class:workspace.notice", "  ( ) Do not run (default)\n"),
        ("", "\n> "),
    ]
    assert workspace._bottom_toolbar() == [  # type: ignore[attr-defined]
        (
            "class:workspace.footer-line",
            " ↑/↓ move, Enter confirm, Esc cancel · Blank selects default: Do not run",
        )
    ]
    assert workspace.snapshot().overlay_title == "Tool access request"


@pytest.mark.asyncio
async def test_chat_workspace_choice_mode_clears_and_restores_existing_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choice prompts should not leak a cancelled composer draft into the selector."""

    @contextmanager
    def _fake_patch_stdout() -> Iterator[None]:
        yield

    class _FakeBuffer:
        def __init__(self) -> None:
            self.text = "partially typed follow-up"
            self.cursor_position = 9

    class _FakePromptSession:
        def __init__(self) -> None:
            self.default_buffer = _FakeBuffer()
            self.completer = object()
            self.complete_while_typing = True
            self.auto_suggest = object()
            self.bottom_toolbar = None
            self.app = SimpleNamespace(is_running=False)

        async def prompt_async(self, message: object) -> str:
            assert self.default_buffer.text == ""
            assert self.default_buffer.cursor_position == 0
            rendered = message() if callable(message) else message
            assert rendered
            return "2"

    monkeypatch.setattr(workspace_app_module, "patch_stdout", _fake_patch_stdout)
    prompt_session = _FakePromptSession()
    workspace = ChatWorkspaceApp(
        prompt_session=prompt_session,  # type: ignore[arg-type]
        emit_output=False,
    )

    selected = await workspace.choose_option(
        title="Tool access request",
        prompt="Approve access to tool: bash.exec?",
        options=(
            ("allow_once", "Run once"),
            ("allow_session", "Allow for session"),
            ("deny", "Do not run"),
        ),
        default_value="deny",
    )

    assert selected == "allow_session"
    assert prompt_session.default_buffer.text == "partially typed follow-up"
    assert prompt_session.default_buffer.cursor_position == 9


def test_chat_workspace_app_can_request_exit_without_running_prompt() -> None:
    """Requesting exit should set the local shutdown flag."""

    workspace = ChatWorkspaceApp(emit_output=False)

    workspace.request_exit()

    assert workspace.exit_requested is True


@pytest.mark.asyncio
async def test_chat_workspace_app_choose_option_accepts_numeric_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choice prompts should accept numeric option input."""

    workspace = ChatWorkspaceApp(emit_output=False)

    async def _fake_prompt_choice_input() -> str:
        return "2"

    monkeypatch.setattr(workspace, "_prompt_choice_mode_input", _fake_prompt_choice_input)

    selected = await workspace.choose_option(
        title="Execution",
        prompt="Execute the task using this plan?",
        options=(("yes", "Execute"), ("no", "Stop")),
        default_value="yes",
    )

    assert selected == "no"


@pytest.mark.asyncio
async def test_chat_workspace_app_choose_option_uses_default_for_blank_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank choice input should resolve to the configured default option."""

    workspace = ChatWorkspaceApp(emit_output=False)

    async def _fake_prompt_choice_input() -> str:
        return ""

    monkeypatch.setattr(workspace, "_prompt_choice_mode_input", _fake_prompt_choice_input)

    selected = await workspace.choose_option(
        title="Execution",
        prompt="Execute the task using this plan?",
        options=(("yes", "Execute"), ("no", "Stop")),
        default_value="no",
    )

    assert selected == "no"


@pytest.mark.asyncio
async def test_chat_workspace_app_confirm_can_return_cancel_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelled confirm prompts should respect the explicit cancel result."""

    workspace = ChatWorkspaceApp(emit_output=False)

    async def _cancel_prompt() -> None:
        return None

    monkeypatch.setattr(workspace, "_prompt_choice_mode_input", _cancel_prompt)

    confirmed = await workspace.confirm(
        title="Execution",
        question="Execute the task using this plan?",
        default=True,
        yes_label="Execute",
        no_label="Stop",
        cancel_result=False,
    )

    assert confirmed is False
