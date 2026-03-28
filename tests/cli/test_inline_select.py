"""Tests for inline select fallbacks outside interactive TTY sessions."""

from __future__ import annotations

import asyncio

from afkbot.cli.presentation.inline_select import (
    _match_single_option,
    confirm_space,
    run_inline_multi_select,
    run_inline_single_select,
)


def test_run_inline_single_select_returns_none_without_tty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Inline single-select should silently fall back when stdin/stdout are not terminals."""

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    result = run_inline_single_select(
        title="Planning",
        text="Create a plan first?",
        options=[("yes", "Yes"), ("no", "No")],
        default_value="yes",
    )

    assert result is None


def test_confirm_space_uses_default_without_tty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Confirm helper should return default without trying to render prompt-toolkit UI."""

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    assert confirm_space(question="Proceed?", default=True, title="Execution") is True
    assert confirm_space(question="Proceed?", default=False, title="Execution") is False


def test_run_inline_multi_select_returns_none_without_tty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Inline multi-select should also fall back cleanly when no TTY is attached."""

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    result = run_inline_multi_select(
        title="Profiles",
        text="Select profiles",
        options=[("default", "default"), ("ops", "ops")],
        default_values=("default",),
    )

    assert result is None


def test_confirm_space_uses_line_prompt_inside_running_event_loop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Confirm helper should keep collecting input when called from an active asyncio loop."""

    # Arrange
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")

    async def _act() -> bool:
        # Act
        return confirm_space(
            question="Create a plan first?",
            default=True,
            title="Planning",
            yes_label="Plan first",
            no_label="Run now",
        )

    # Assert
    assert asyncio.run(_act()) is False


def test_run_inline_multi_select_uses_line_prompt_inside_running_event_loop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Multi-select should parse comma-separated text input when async prompt-toolkit UI is unavailable."""

    # Arrange
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2,1")

    async def _act() -> list[str] | None:
        # Act
        return run_inline_multi_select(
            title="Profiles",
            text="Select profiles",
            options=[("default", "default"), ("ops", "ops")],
            default_values=("default",),
        )

    # Assert
    assert asyncio.run(_act()) == ["ops", "default"]


def test_match_single_option_rejects_unicode_digits_that_int_cannot_parse() -> None:
    """Unicode digit-like input should not crash line-based option matching."""

    # Arrange
    options = [("yes", "Yes"), ("no", "No")]

    # Act
    result = _match_single_option(answer="²", options=options)

    # Assert
    assert result is None
