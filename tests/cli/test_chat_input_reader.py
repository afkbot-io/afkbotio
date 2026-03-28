"""Tests for async chat input capture helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from afkbot.cli.presentation.chat_input import ChatInputReader


@pytest.mark.asyncio
async def test_chat_input_reader_calls_prompt_async_with_dynamic_message_and_toggles_prompt_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async prompt capture should pass the configured prompt message and toggle activity hooks."""

    # Arrange
    activity_calls: list[bool] = []
    patch_calls: list[str] = []
    seen_messages: list[str] = []

    @contextmanager
    def _fake_patch_stdout() -> Iterator[None]:
        patch_calls.append("enter")
        try:
            yield
        finally:
            patch_calls.append("exit")

    class _FakePromptSession:
        async def prompt_async(self, message: object) -> str:
            rendered = message() if callable(message) else message
            seen_messages.append(str(rendered))
            return "queued request"

    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_input.patch_stdout",
        _fake_patch_stdout,
    )
    reader = ChatInputReader(
        prompt_session=_FakePromptSession(),  # type: ignore[arg-type]
        on_prompt_activity=activity_calls.append,
        prompt_message=lambda: "Summary: plan=auto\nActivity: thinking\nyou > ",
    )

    # Act
    result = await reader.read_input()

    # Assert
    assert result == "queued request"
    assert seen_messages == ["Summary: plan=auto\nActivity: thinking\nyou > "]
    assert activity_calls == [True, False]
    assert patch_calls == ["enter", "exit"]


@pytest.mark.asyncio
async def test_chat_input_reader_recomputes_message_each_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt message should be evaluated again for every prompt read."""

    # Arrange
    patch_calls: list[str] = []
    seen_messages: list[str] = []
    message_values = iter(
        (
            "Summary: plan=auto\nActivity: starting\nyou > ",
            "Summary: plan=on\nActivity: response ready\nyou > ",
        )
    )

    @contextmanager
    def _fake_patch_stdout() -> Iterator[None]:
        patch_calls.append("patch")
        yield

    class _FakePromptSession:
        async def prompt_async(self, message: object) -> str:
            rendered = message() if callable(message) else message
            seen_messages.append(str(rendered))
            return "ok"

    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_input.patch_stdout",
        _fake_patch_stdout,
    )
    reader = ChatInputReader(
        prompt_session=_FakePromptSession(),  # type: ignore[arg-type]
        prompt_message=lambda: next(message_values),
    )

    # Act
    await reader.read_input()
    await reader.read_input()

    # Assert
    assert seen_messages == [
        "Summary: plan=auto\nActivity: starting\nyou > ",
        "Summary: plan=on\nActivity: response ready\nyou > ",
    ]
    assert patch_calls == ["patch", "patch"]


@pytest.mark.asyncio
async def test_chat_input_reader_falls_back_to_sync_prompt_with_configured_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync prompt fallback should pass through the same configured prompt message."""

    # Arrange
    seen_messages: list[str] = []

    async def _fake_to_thread(func: object, *args: object) -> str:
        return func(*args)  # type: ignore[misc]

    class _FakePromptSession:
        def prompt(self, message: object) -> str:
            rendered = message() if callable(message) else message
            seen_messages.append(str(rendered))
            return "sync request"

    monkeypatch.setattr("afkbot.cli.presentation.chat_input.asyncio.to_thread", _fake_to_thread)
    reader = ChatInputReader(
        prompt_session=_FakePromptSession(),  # type: ignore[arg-type]
        prompt_message=lambda: "Summary: plan=auto\nyou > ",
    )

    # Act
    result = await reader.read_input()

    # Assert
    assert result == "sync request"
    assert seen_messages == ["Summary: plan=auto\nyou > "]
