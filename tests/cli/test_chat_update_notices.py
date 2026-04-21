"""Tests for chat-time update notice behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.cli.commands.chat_update_notices import _should_prompt_for_update, handle_chat_update_notice
from afkbot.services.update_runtime import UpdateAvailability, UpdateResult
from afkbot.settings import get_settings


def _prepare_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    return get_settings()


def test_handle_chat_update_notice_persists_skip_choice(tmp_path, monkeypatch) -> None:
    """Choosing no should continue chat without persisting a suppression marker."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    captured: list[dict[str, object]] = []
    availability = UpdateAvailability(
        install_mode="uv-tool",
        current_version="afk 1.0.0",
        target_id="package:afkbotio:1.4.2",
        target_label="afkbotio 1.4.2",
        details=(),
    )

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.inspect_available_update",
        lambda _settings: availability,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.prompt_chat_update_action",
        lambda **_kwargs: "skip",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.write_runtime_config",
        lambda _settings, *, config: captured.append(dict(config)),
    )

    should_continue = handle_chat_update_notice(settings=settings)

    assert should_continue is True
    assert captured[-1]["update_notice_skip_target"] is None
    assert captured[-1]["update_notice_remind_target"] is None
    assert captured[-1]["update_notice_remind_until"] is None


def test_handle_chat_update_notice_persists_remind_week_choice(tmp_path, monkeypatch) -> None:
    """Remind-later choice should store one global snooze window."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    captured: list[dict[str, object]] = []
    availability = UpdateAvailability(
        install_mode="uv-tool",
        current_version="afk 1.0.0",
        target_id="package:afkbotio:1.4.2",
        target_label="afkbotio 1.4.2",
        details=(),
    )

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.inspect_available_update",
        lambda _settings: availability,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.prompt_chat_update_action",
        lambda **_kwargs: "remind_week",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.write_runtime_config",
        lambda _settings, *, config: captured.append(dict(config)),
    )

    should_continue = handle_chat_update_notice(settings=settings)

    assert should_continue is True
    assert captured[-1]["update_notice_remind_target"] is None
    assert captured[-1]["update_notice_remind_until"] is not None


def test_handle_chat_update_notice_runs_update_and_stops_chat(tmp_path, monkeypatch) -> None:
    """Choosing install should run update and ask the operator to restart chat."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    captured: list[dict[str, object]] = []
    echoed: list[str] = []
    availability = UpdateAvailability(
        install_mode="host",
        current_version="afk 1.0.0",
        target_id="git:main:abcdef123456",
        target_label="origin/main @ abcdef123456",
        details=(),
    )

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.inspect_available_update",
        lambda _settings: availability,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.prompt_chat_update_action",
        lambda **_kwargs: "install",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.resolve_prompt_language",
        lambda **_kwargs: PromptLanguage.EN,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.run_update",
        lambda _settings: UpdateResult(
            install_mode="host",
            source_updated=True,
            runtime_restarted=False,
            maintenance_applied=True,
            details=("Git branch: main",),
        ),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.write_runtime_config",
        lambda _settings, *, config: captured.append(dict(config)),
    )
    monkeypatch.setattr("afkbot.cli.commands.chat_update_notices.typer.echo", echoed.append)

    should_continue = handle_chat_update_notice(settings=settings)

    assert should_continue is False
    assert captured[-1]["update_notice_remind_target"] is None
    assert captured[-1]["update_notice_remind_until"] is None
    assert any("AFKBOT update complete." in item for item in echoed)
    assert any("Restart `afk chat`" in item for item in echoed)


def test_handle_chat_update_notice_localizes_success_summary_in_russian(tmp_path, monkeypatch) -> None:
    """Russian prompt language should localize the post-update success summary too."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    echoed: list[str] = []
    availability = UpdateAvailability(
        install_mode="host",
        current_version="afk 1.0.0",
        target_id="git:main:abcdef123456",
        target_label="origin/main @ abcdef123456",
        details=(),
    )

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.inspect_available_update",
        lambda _settings: availability,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.prompt_chat_update_action",
        lambda **_kwargs: "install",
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.resolve_prompt_language",
        lambda **_kwargs: PromptLanguage.RU,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.run_update",
        lambda _settings: UpdateResult(
            install_mode="host",
            source_updated=True,
            runtime_restarted=False,
            maintenance_applied=True,
            details=("Git branch: main",),
        ),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.write_runtime_config",
        lambda _settings, *, config: None,
    )
    monkeypatch.setattr("afkbot.cli.commands.chat_update_notices.typer.echo", echoed.append)

    should_continue = handle_chat_update_notice(settings=settings)

    assert should_continue is False
    assert any("Обновление AFKBOT завершено." in item for item in echoed)
    assert any("Режим установки: host" in item for item in echoed)
    assert any("Git-ветка: main" in item for item in echoed)
    assert any("Перезапустите `afk chat`" in item for item in echoed)


def test_should_prompt_for_update_respects_future_remind_deadline() -> None:
    """Remind-later choice should suppress all prompts until the deadline passes."""

    future = datetime.now(tz=UTC) + timedelta(days=3)

    assert _should_prompt_for_update(
        runtime_config={
            "update_notice_remind_until": future.isoformat(),
        },
    ) is False
    assert _should_prompt_for_update(
        runtime_config={
            "update_notice_remind_until": (future - timedelta(days=10)).isoformat(),
        },
    ) is True


def test_should_prompt_for_update_ignores_legacy_target_only_state() -> None:
    """Legacy target-only state should not keep suppressing prompts."""

    future = datetime.now(tz=UTC) + timedelta(days=3)

    assert _should_prompt_for_update(
        runtime_config={
            "update_notice_remind_target": "package:afkbotio:1.4.2",
            "update_notice_skip_target": "package:afkbotio:1.4.2",
            "update_notice_remind_until": future.isoformat(),
        },
    ) is False
    assert _should_prompt_for_update(
        runtime_config={
            "update_notice_remind_target": "package:afkbotio:1.4.2",
            "update_notice_skip_target": "package:afkbotio:1.4.2",
        },
    ) is True


def test_handle_chat_update_notice_skips_prompt_when_notices_disabled(tmp_path, monkeypatch) -> None:
    """Disabled notices should bypass the startup prompt entirely."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    inspected: list[object] = []
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.inspect_available_update",
        lambda _settings: inspected.append(True),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.read_runtime_config",
        lambda _settings: {"update_notices_enabled": False},
    )
    prompted: list[object] = []
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.prompt_chat_update_action",
        lambda **_kwargs: prompted.append(True),
    )

    should_continue = handle_chat_update_notice(settings=settings)

    assert should_continue is True
    assert inspected == []
    assert prompted == []


def test_handle_chat_update_notice_skips_inspection_during_active_snooze(tmp_path, monkeypatch) -> None:
    """Active snooze should bypass update inspection until the deadline passes."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    inspected: list[object] = []
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.inspect_available_update",
        lambda _settings: inspected.append(True),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_update_notices.read_runtime_config",
        lambda _settings: {
            "update_notices_enabled": True,
            "update_notice_remind_until": (datetime.now(tz=UTC) + timedelta(days=2)).isoformat(),
        },
    )

    should_continue = handle_chat_update_notice(settings=settings)

    assert should_continue is True
    assert inspected == []
