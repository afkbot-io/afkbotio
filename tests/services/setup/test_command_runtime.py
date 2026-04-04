"""Tests for localized setup completion messaging."""

from __future__ import annotations

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.setup.command_runtime import render_setup_success


def test_render_setup_success_in_english(capsys) -> None:
    """Interactive setup should explain the next commands in English."""

    render_setup_success(
        interactive=True,
        prompt_language=PromptLanguage.EN,
        response=None,
    )

    output = capsys.readouterr().out
    assert "AFKBOT setup is complete." in output
    assert "The default profile is ready and saved for future chats." in output
    assert "Next, check local health:" in output
    assert "  afk doctor" in output
    assert "Then open chat and start working with AFKBOT:" in output
    assert "  afk chat" in output
    assert "Inside `afk chat`, describe the task in natural language." in output


def test_render_setup_success_in_russian(capsys) -> None:
    """Interactive setup should explain the next commands in Russian."""

    render_setup_success(
        interactive=True,
        prompt_language=PromptLanguage.RU,
        response=None,
    )

    output = capsys.readouterr().out
    assert "Настройка AFKBOT завершена." in output
    assert "Профиль по умолчанию готов и сохранён для следующих чатов." in output
    assert "Теперь проверьте локальное состояние:" in output
    assert "  afk doctor" in output
    assert "Затем откройте чат и начните работать с AFKBOT:" in output
    assert "  afk chat" in output
    assert "Внутри `afk chat` просто опишите задачу обычным языком." in output
