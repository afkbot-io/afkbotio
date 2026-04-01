"""Tests for chat workspace transcript helpers."""

from __future__ import annotations

from afkbot.cli.presentation.chat_workspace.transcript import (
    render_chat_workspace_transcript,
    ChatWorkspaceTranscript,
    ChatWorkspaceTranscriptEntry,
    render_chat_workspace_transcript_text,
)


def test_render_chat_workspace_transcript_text_formats_multiple_entry_types() -> None:
    """Transcript rendering should group each entry into one readable block."""

    # Arrange
    entries = [
        ChatWorkspaceTranscriptEntry(kind="user", text="run tests"),
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Ready to help."),
        ChatWorkspaceTranscriptEntry(kind="notice", text="Queued next message. Pending queue: 1"),
        ChatWorkspaceTranscriptEntry(kind="plan", text="[ ] Inspect\n[x] Verify"),
    ]

    # Act
    rendered = render_chat_workspace_transcript_text(entries)

    # Assert
    assert rendered == (
        "you > run tests\n\n"
        "Ready to help.\n\n"
        "• Queued next message. Pending queue: 1\n\n"
        "Proposed Plan\n"
        "[ ] Inspect\n"
        "[x] Verify"
    )


def test_render_chat_workspace_transcript_keeps_tight_progress_groups_without_blank_lines() -> None:
    """Tight progress entries should stack without empty spacer lines."""

    # Arrange
    entries = [
        ChatWorkspaceTranscriptEntry(kind="user", text="run tests"),
        ChatWorkspaceTranscriptEntry(
            kind="assistant",
            text="[iter 1] thinking...",
            accent="thinking",
            spacing_before="normal",
        ),
        ChatWorkspaceTranscriptEntry(
            kind="assistant",
            text="[#1] calling tool: bash.exec",
            accent="tool",
            spacing_before="tight",
        ),
        ChatWorkspaceTranscriptEntry(
            kind="assistant",
            text="cmd=pytest",
            accent="detail",
            spacing_before="tight",
        ),
    ]

    # Act
    rendered = render_chat_workspace_transcript_text(entries)

    # Assert
    assert rendered == (
        "you > run tests\n\n"
        "[iter 1] thinking...\n"
        "[#1] calling tool: bash.exec\n"
        "cmd=pytest"
    )


def test_render_chat_workspace_transcript_text_preserves_single_blank_lines_between_paragraphs() -> None:
    """Assistant transcript text should keep one blank line between paragraphs."""

    # Arrange
    entries = [
        ChatWorkspaceTranscriptEntry(
            kind="assistant",
            text="First paragraph.\n\n\nSecond paragraph.",
        )
    ]

    # Act
    rendered = render_chat_workspace_transcript_text(entries)

    # Assert
    assert rendered == "First paragraph.\n\nSecond paragraph."


def test_chat_workspace_transcript_renders_empty_state() -> None:
    """Empty transcripts should keep the transcript surface blank."""

    # Arrange
    transcript = ChatWorkspaceTranscript()

    # Act
    rendered = transcript.render_text()

    # Assert
    assert rendered == ""


def test_render_chat_workspace_transcript_styles_user_entries_as_classic_prompt_rows() -> None:
    """User transcript entries should render as classic AFK `you >` rows."""

    # Arrange
    entries = [ChatWorkspaceTranscriptEntry(kind="user", text="hello")]

    # Act
    rendered = render_chat_workspace_transcript(entries, width=24)

    # Assert
    assert rendered.plain_text == "you > hello"
    assert rendered.line_count == 1
    assert rendered.fragments == [
        ("class:workspace.user-label", "you"),
        ("class:workspace.user-separator", " > "),
        ("class:workspace.user-text", "hello"),
    ]


def test_render_chat_workspace_transcript_keeps_complete_history() -> None:
    """Transcript rendering should keep the complete history in order."""

    # Arrange
    entries = [
        ChatWorkspaceTranscriptEntry(kind="assistant", text="First"),
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Second"),
        ChatWorkspaceTranscriptEntry(kind="assistant", text="Third"),
    ]

    # Act
    rendered = render_chat_workspace_transcript(entries, width=24)

    # Assert
    assert rendered.plain_text == "First\n\nSecond\n\nThird"
    assert rendered.line_count == 5
