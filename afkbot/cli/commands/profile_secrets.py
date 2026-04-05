"""Profile-local runtime secrets CLI commands."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.profile_common import emit_profile_error
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.profile_runtime import (
    ProfileServiceError,
    get_profile_runtime_secrets_service,
    get_profile_service,
    provider_oauth_metadata_fields,
    provider_secret_field,
)
from afkbot.settings import get_settings


def register_secrets(profile_app: typer.Typer) -> None:
    """Register `afk profile secrets ...` commands."""

    secrets_app = typer.Typer(
        help="Manage profile-local encrypted runtime secrets.",
        no_args_is_help=True,
    )

    @secrets_app.command("show")
    def show(profile_id: str = typer.Argument(..., help="Runtime profile id.")) -> None:
        """Show which runtime secret fields are configured for one profile."""

        settings = get_settings()
        try:
            normalized_profile_id = validate_profile_id(profile_id)
            profile = asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "profile_id": normalized_profile_id,
                    "llm_provider": profile.effective_runtime.llm_provider,
                    "runtime_secrets": profile.runtime_secrets.model_dump(mode="json"),
                    "runtime_secrets_path": profile.runtime_secrets_path,
                },
                ensure_ascii=True,
            )
        )

    @secrets_app.command("set")
    def set_secret(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
        llm_api_key: str | None = typer.Option(
            None,
            "--llm-api-key",
            help="Generic fallback provider credential (API key or OAuth token) for this profile.",
            hide_input=True,
        ),
        provider_api_key: str | None = typer.Option(
            None,
            "--provider-api-key",
            help="Provider-specific credential (API key or OAuth token) for this profile's current provider.",
            hide_input=True,
        ),
        brave_api_key: str | None = typer.Option(
            None,
            "--brave-api-key",
            help="Brave Search API key for this profile's web.search runtime.",
            hide_input=True,
        ),
    ) -> None:
        """Set or update encrypted runtime secrets for one profile."""

        settings = get_settings()
        try:
            normalized_profile_id = validate_profile_id(profile_id)
            profile = asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            updates = _build_secret_updates(
                llm_provider=profile.effective_runtime.llm_provider,
                llm_api_key=llm_api_key,
                provider_api_key=provider_api_key,
                brave_api_key=brave_api_key,
            )
            if not updates:
                raise_usage_error(
                    "Provide at least one of --llm-api-key, --provider-api-key, or --brave-api-key."
                )
            service = get_profile_runtime_secrets_service(settings)
            service.merge(normalized_profile_id, updates)
            refreshed = asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "profile_id": normalized_profile_id,
                    "runtime_secrets": refreshed.runtime_secrets.model_dump(mode="json"),
                    "runtime_secrets_path": refreshed.runtime_secrets_path,
                },
                ensure_ascii=True,
            )
        )

    @secrets_app.command("clear")
    def clear_secret(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
        all_fields: bool = typer.Option(
            False,
            "--all",
            help="Remove all profile-local provider secrets.",
        ),
        llm_api_key: bool = typer.Option(
            False,
            "--llm-api-key",
            help="Clear the generic fallback API key.",
        ),
        provider_api_key: bool = typer.Option(
            False,
            "--provider-api-key",
            help="Clear the provider-specific credential for the profile's current provider.",
        ),
        brave_api_key: bool = typer.Option(
            False,
            "--brave-api-key",
            help="Clear the Brave Search API key for this profile.",
        ),
    ) -> None:
        """Clear selected or all profile-local runtime secrets."""

        settings = get_settings()
        try:
            normalized_profile_id = validate_profile_id(profile_id)
            profile = asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            if all_fields:
                fields_to_clear: tuple[str, ...] | None = None
            else:
                requested_fields: list[str] = []
                if llm_api_key:
                    requested_fields.append("llm_api_key")
                if provider_api_key:
                    requested_fields.append(provider_secret_field(profile.effective_runtime.llm_provider))
                    requested_fields.extend(
                        provider_oauth_metadata_fields(profile.effective_runtime.llm_provider)
                    )
                if brave_api_key:
                    requested_fields.append("brave_api_key")
                if not requested_fields:
                    raise_usage_error("Use --all, --llm-api-key, --provider-api-key, or --brave-api-key.")
                fields_to_clear = tuple(dict.fromkeys(requested_fields))
            service = get_profile_runtime_secrets_service(settings)
            service.clear(normalized_profile_id, fields=fields_to_clear)
            refreshed = asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "profile_id": normalized_profile_id,
                    "runtime_secrets": refreshed.runtime_secrets.model_dump(mode="json"),
                    "runtime_secrets_path": refreshed.runtime_secrets_path,
                },
                ensure_ascii=True,
            )
        )

    profile_app.add_typer(secrets_app, name="secrets")


def _build_secret_updates(
    *,
    llm_provider: str,
    llm_api_key: str | None,
    provider_api_key: str | None,
    brave_api_key: str | None,
) -> dict[str, str]:
    """Translate CLI flags into normalized profile-local secret updates."""

    updates: dict[str, str] = {}
    if llm_api_key is not None and llm_api_key.strip():
        updates["llm_api_key"] = llm_api_key.strip()
    if provider_api_key is not None and provider_api_key.strip():
        updates[provider_secret_field(llm_provider)] = provider_api_key.strip()
    if brave_api_key is not None and brave_api_key.strip():
        updates["brave_api_key"] = brave_api_key.strip()
    return updates
