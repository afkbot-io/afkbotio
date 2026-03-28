"""Presentation helpers for profile mutation success output."""

from __future__ import annotations

import typer

from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.services.profile_runtime import ProfileDetails


def render_profile_mutation_success(
    *,
    profile: ProfileDetails,
    lang: PromptLanguage,
    verb_en: str,
    verb_ru: str,
) -> None:
    """Print concise interactive success summary for profile create/update."""

    details = profile.model_dump(mode="json")
    effective = details["effective_runtime"]
    typer.echo(
        msg(
            lang,
            en=f"Profile `{details['id']}` {verb_en} successfully.",
            ru=f"Профиль `{details['id']}` успешно {verb_ru}.",
        )
    )
    typer.echo(
        msg(
            lang,
            en=f"Provider/model: {effective['llm_provider']} / {effective['llm_model']}",
            ru=f"Провайдер/модель: {effective['llm_provider']} / {effective['llm_model']}",
        )
    )
    typer.echo(
        msg(
            lang,
            en=f"Next steps: run `afk chat --profile {details['id']}` or inspect `afk profile show {details['id']}`.",
            ru=f"Дальше: запустите `afk chat --profile {details['id']}` или проверьте `afk profile show {details['id']}`.",
        )
    )


__all__ = ["render_profile_mutation_success"]
