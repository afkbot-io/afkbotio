"""Tests for chat result rendering blocks."""

from __future__ import annotations

from typing import Literal

from afkbot.cli.presentation.chat_renderer import render_chat_result
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult


def _result(message: str, *, action: Literal["finalize", "block"] = "finalize") -> TurnResult:
    return TurnResult(
        run_id=1,
        session_id="s",
        profile_id="default",
        envelope=ActionEnvelope(action=action, message=message),
    )


def test_renderer_does_not_mark_plain_error_word_as_failure() -> None:
    """Benign text containing the word error should keep AFK Agent style."""

    rendered = render_chat_result(_result("This is not an error, just a note."))
    assert "AFK Agent" in rendered
    assert "ERROR" not in rendered


def test_renderer_marks_structured_error_payload_as_error_block() -> None:
    """Structured provider error payload should render red ERROR block."""

    message = (
        '[Error: request could not be processed] '
        '{"error":{"message":"bad","type":"BadRequestError","code":400}}'
    )
    rendered = render_chat_result(_result(message))

    assert "ERROR" in rendered
    assert "message: bad" in rendered
    assert "type: BadRequestError" in rendered


def test_renderer_highlights_diff_blocks_for_terminal_output() -> None:
    """TTY rendering should strip diff fences and colorize unified diff lines."""

    rendered = render_chat_result(
        _result(
            "\n".join(
                (
                    "Done.",
                    "",
                    "### Diff:",
                    "```diff",
                    "--- before.txt",
                    "+++ after.txt",
                    "@@ -1 +1 @@",
                    "-old",
                    "+new",
                    "```",
                )
            )
        ),
        ansi=True,
    )

    assert "```diff" not in rendered
    assert "```" not in rendered
    assert "\033[96m--- before.txt\033[0m" in rendered
    assert "\033[96m+++ after.txt\033[0m" in rendered
    assert "\033[93m@@ -1 +1 @@\033[0m" in rendered
    assert "\033[91m-old\033[0m" in rendered
    assert "\033[92m+new\033[0m" in rendered


def test_renderer_keeps_plain_diff_markdown_without_ansi() -> None:
    """Non-TTY rendering should preserve the original fenced diff markdown."""

    message = "\n".join(
        (
            "### Diff:",
            "```diff",
            "--- before.txt",
            "+++ after.txt",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "```",
        )
    )

    rendered = render_chat_result(_result(message), ansi=False)

    assert "```diff" in rendered
    assert "--- before.txt" in rendered
    assert "+new" in rendered
