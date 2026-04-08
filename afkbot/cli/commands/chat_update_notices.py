"""Chat-time update notice prompts and persisted reminder state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import typer

from afkbot.cli.presentation.update_prompts import ChatUpdateAction, prompt_chat_update_action
from afkbot.cli.presentation.prompt_i18n import msg, resolve_prompt_language
from afkbot.services.setup.defaults import coerce_bool
from afkbot.services.setup.runtime_store import read_runtime_config, write_runtime_config
from afkbot.services.update_runtime import (
    UpdateRuntimeError,
    format_update_success,
    inspect_available_update,
    run_update,
)
from afkbot.settings import Settings

_REMIND_FOR_DAYS = 7


def handle_chat_update_notice(*, settings: Settings) -> bool:
    """Prompt about one available update before chat starts.

    Returns `True` when chat should continue and `False` when the command should stop
    after handling the update path.
    """

    runtime_config = dict(read_runtime_config(settings))
    if not _update_notices_enabled(runtime_config):
        return True
    if not _should_prompt_for_update(runtime_config=runtime_config):
        return True

    availability = inspect_available_update(settings)
    if availability is None:
        return True

    choice = prompt_chat_update_action(
        availability=availability,
        lang=resolve_prompt_language(settings=settings, value=None, ru=False),
    )
    if choice is None:
        return True
    if choice == ChatUpdateAction.SKIP:
        _persist_notice_state(
            settings=settings,
            runtime_config=runtime_config,
            remind_until=None,
        )
        return True
    if choice == ChatUpdateAction.REMIND_WEEK:
        remind_until = datetime.now(tz=UTC) + timedelta(days=_REMIND_FOR_DAYS)
        _persist_notice_state(
            settings=settings,
            runtime_config=runtime_config,
            remind_until=remind_until,
        )
        return True

    lang = resolve_prompt_language(settings=settings, value=None, ru=False)
    try:
        result = run_update(settings)
    except UpdateRuntimeError as exc:
        typer.echo(
            msg(
                lang,
                en=f"AFKBOT update failed: {exc.reason}",
                ru=f"Не удалось обновить AFKBOT: {exc.reason}",
            )
        )
        return True

    _persist_notice_state(
        settings=settings,
        runtime_config=runtime_config,
        remind_until=None,
    )
    typer.echo(format_update_success(result))
    typer.echo(
        msg(
            lang,
            en="Restart `afk chat` to continue on the updated version.",
            ru="Перезапустите `afk chat`, чтобы продолжить уже на обновлённой версии.",
        )
    )
    return False


def _update_notices_enabled(runtime_config: dict[str, object]) -> bool:
    return coerce_bool(runtime_config.get("update_notices_enabled"), default=True)


def _should_prompt_for_update(*, runtime_config: dict[str, object]) -> bool:
    remind_until = _parse_timestamp(runtime_config.get("update_notice_remind_until"))
    if remind_until is not None and remind_until > datetime.now(tz=UTC):
        return False
    return True


def _persist_notice_state(
    *,
    settings: Settings,
    runtime_config: dict[str, object],
    remind_until: datetime | None,
) -> None:
    runtime_config["update_notice_skip_target"] = None
    runtime_config["update_notice_remind_target"] = None
    runtime_config["update_notice_remind_until"] = (
        remind_until.isoformat() if remind_until is not None else None
    )
    write_runtime_config(settings, config=runtime_config)


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["_should_prompt_for_update", "handle_chat_update_notice"]
