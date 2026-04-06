"""Context resolution helpers for Telethon update CLI flows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from afkbot.cli.commands.channel_shared import (
    load_channel_profile,
    resolve_channel_update_profile_id,
    should_collect_channel_update_interactively,
)
from afkbot.cli.commands.channel_telethon_commands.legacy import (
    load_legacy_telethon_endpoint,
)
from afkbot.cli.presentation.setup_prompts import PromptLanguage
from afkbot.cli.presentation.setup_prompts import resolve_prompt_language
from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.settings import Settings, get_settings


@dataclass(frozen=True, slots=True)
class TelethonUpdateContext:
    """Resolved context shared across the Telethon update flow."""

    settings: Settings
    current: TelethonUserEndpointConfig
    interactive: bool
    prompt_language: PromptLanguage
    resolved_profile_id: str


def resolve_telethon_update_context(
    *,
    channel_id: str,
    profile_id: str | None,
    yes: bool,
    lang: str | None,
    ru: bool,
    sync_binding: bool,
    values: tuple[object | None, ...],
) -> TelethonUpdateContext:
    """Resolve the current endpoint and interactive update defaults."""

    settings = get_settings()
    current = asyncio.run(load_legacy_telethon_endpoint(channel_id=channel_id))
    prompt_language = resolve_prompt_language(settings=settings, value=lang, ru=ru)
    interactive = should_collect_channel_update_interactively(
        yes=yes,
        sync_binding=sync_binding,
        values=values,
    )
    resolved_profile_id = resolve_channel_update_profile_id(
        profile_id=profile_id,
        current_profile_id=current.profile_id,
    )
    load_channel_profile(
        settings=settings,
        profile_id=resolved_profile_id,
    )
    return TelethonUpdateContext(
        settings=settings,
        current=current,
        interactive=interactive,
        prompt_language=prompt_language,
        resolved_profile_id=resolved_profile_id,
    )


__all__ = ["TelethonUpdateContext", "resolve_telethon_update_context"]
