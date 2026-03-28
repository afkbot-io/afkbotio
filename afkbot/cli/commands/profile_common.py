"""Shared helpers for profile CLI commands."""

from __future__ import annotations

import json

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.setup.provider_inputs import resolve_text
from afkbot.services.naming import normalize_runtime_name
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.profile_runtime import ProfileServiceError


def resolve_profile_id(
    *,
    value: str | None,
    interactive: bool,
    lang: PromptLanguage,
) -> str:
    """Resolve and validate one profile identifier."""

    if value is None and not interactive:
        raise_usage_error("Use --id in --yes mode.")
    raw_value = resolve_text(
        value=value,
        interactive=interactive,
        prompt=msg(lang, en="Profile id", ru="ID профиля"),
        default="",
        lang=lang,
    )
    normalized = normalize_runtime_name(raw_value, max_length=64)
    return validate_profile_id(normalized)


def resolve_profile_name(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve human-readable profile name."""

    return resolve_text(
        value=value,
        interactive=interactive,
        prompt=msg(lang, en="Profile name", ru="Название профиля"),
        default=default,
        lang=lang,
    )


def emit_profile_error(exc: Exception) -> None:
    """Render CLI-safe JSON error payload for profile commands."""

    if isinstance(exc, ProfileServiceError):
        payload = {
            "ok": False,
            "error_code": exc.error_code,
            "reason": exc.reason,
        }
    elif isinstance(exc, ChannelBindingServiceError):
        payload = {
            "ok": False,
            "error_code": exc.error_code,
            "reason": exc.reason,
        }
    elif isinstance(exc, InvalidProfileIdError):
        payload = {
            "ok": False,
            "error_code": "profile_invalid_id",
            "reason": str(exc),
        }
    elif isinstance(exc, FileNotFoundError):
        payload = {
            "ok": False,
            "error_code": "profile_file_not_found",
            "reason": str(exc),
        }
    else:
        payload = {
            "ok": False,
            "error_code": "profile_invalid_config",
            "reason": str(exc),
        }
    typer.echo(json.dumps(payload, ensure_ascii=True))
