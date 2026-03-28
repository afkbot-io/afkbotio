"""Top-level CLI commands for profile and marketplace skills."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import typer

from afkbot.cli.commands.runtime_assets_common import emit_structured_error, resolve_inline_or_file_text
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.profile_runtime.service import ProfileServiceError, get_profile_service
from afkbot.services.skills import get_skill_doctor_service
from afkbot.services.skills.marketplace_contracts import (
    SkillMarketplaceError,
)
from afkbot.services.skills.marketplace_payloads import (
    marketplace_install_record_to_payload,
    marketplace_list_item_to_payload,
    marketplace_source_stats_to_payload,
)
from afkbot.services.skills.marketplace_service import get_skill_marketplace_service
from afkbot.services.skills.profile_service import get_profile_skill_service
from afkbot.settings import get_settings

SkillScope = Literal["all", "profile", "core"]


def register(app: typer.Typer) -> None:
    """Register `afk skill ...` commands."""

    skill_app = typer.Typer(
        help="Manage profile-local skills and install skills from marketplaces.",
        no_args_is_help=True,
    )
    app.add_typer(skill_app, name="skill")

    @skill_app.command("list")
    def list_skills(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        scope: SkillScope = typer.Option("all", "--scope", help="Visible scope: all, profile, core."),
        include_unavailable: bool = typer.Option(
            False,
            "--include-unavailable",
            help="Include unavailable skills with missing requirements.",
        ),
    ) -> None:
        """List visible skills for one profile."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            items = asyncio.run(
                get_profile_skill_service(settings).list(
                    profile_id=normalized_profile_id,
                    scope=scope,
                    include_unavailable=include_unavailable,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, SkillMarketplaceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"skills": [item.model_dump(mode="json") for item in items]},
                ensure_ascii=True,
            )
        )

    @skill_app.command("show")
    def show_skill(
        name: str = typer.Argument(..., help="Skill name."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        scope: SkillScope = typer.Option("all", "--scope", help="Visible scope: all, profile, core."),
    ) -> None:
        """Show one skill markdown and metadata."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_profile_skill_service(settings).get(
                    profile_id=normalized_profile_id,
                    name=name,
                    scope=scope,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"skill": item.model_dump(mode="json")}, ensure_ascii=True))

    @skill_app.command("doctor")
    def doctor_skills(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        repair_manifests: bool = typer.Option(
            False,
            "--repair-manifests",
            help="Create or repair missing profile-local AFKBOT manifests before inspection.",
        ),
    ) -> None:
        """Inspect skill manifests, execution modes, and dependency health."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            repairs: list[dict[str, object]] = []
            if repair_manifests:
                repaired = asyncio.run(
                    get_profile_skill_service(settings).normalize_manifests(
                        profile_id=normalized_profile_id,
                    )
                )
                repairs = [item.model_dump(mode="json") for item in repaired]
            items = asyncio.run(
                get_skill_doctor_service(settings).inspect_profile(profile_id=normalized_profile_id)
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "repairs": repairs,
                    "skills": [asdict(item) for item in items],
                },
                ensure_ascii=True,
            )
        )

    @skill_app.command("normalize")
    def normalize_skills(
        name: str | None = typer.Argument(
            None,
            help="Optional profile-local skill name. Omit to normalize all profile-local skills.",
        ),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        overwrite: bool = typer.Option(
            False,
            "--overwrite",
            help="Regenerate valid manifests too instead of only creating or repairing them.",
        ),
    ) -> None:
        """Create or repair AFKBOT manifests for profile-local skills."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            items = asyncio.run(
                get_profile_skill_service(settings).normalize_manifests(
                    profile_id=normalized_profile_id,
                    name=name,
                    overwrite=overwrite,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"skills": [item.model_dump(mode="json") for item in items]},
                ensure_ascii=True,
            )
        )

    @skill_app.command("repair")
    def repair_skills(
        name: str | None = typer.Argument(
            None,
            help="Optional profile-local skill name. Omit to repair all profile-local skills.",
        ),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        overwrite: bool = typer.Option(
            False,
            "--overwrite",
            help="Regenerate valid manifests too instead of only repairing missing or invalid ones.",
        ),
    ) -> None:
        """Repair AFKBOT manifests for profile-local skills."""

        normalize_skills(name=name, profile_id=profile_id, overwrite=overwrite)

    @skill_app.command("set")
    def set_skill(
        name: str = typer.Argument(..., help="Profile-local skill name."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        text: str | None = typer.Option(None, "--text", help="Inline skill markdown."),
        from_file: Path | None = typer.Option(
            None,
            "--from-file",
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
            help="Read skill markdown from a local file.",
        ),
    ) -> None:
        """Create or replace one profile-local skill."""

        try:
            markdown = resolve_inline_or_file_text(text=text, from_file=from_file)
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_profile_skill_service(settings).upsert(
                    profile_id=normalized_profile_id,
                    name=name,
                    content=markdown,
                )
            )
        except (InvalidProfileIdError, OSError, ProfileServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"skill": item.model_dump(mode="json")}, ensure_ascii=True))

    @skill_app.command("delete")
    def delete_skill(
        name: str = typer.Argument(..., help="Profile-local skill name."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
    ) -> None:
        """Delete one profile-local skill."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_profile_skill_service(settings).delete(
                    profile_id=normalized_profile_id,
                    name=name,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"skill": item.model_dump(mode="json")}, ensure_ascii=True))

    marketplace_app = typer.Typer(
        help="Discover and install skills from skills.sh or GitHub-compatible sources.",
        no_args_is_help=True,
    )
    skill_app.add_typer(marketplace_app, name="marketplace")

    @marketplace_app.command("list")
    def list_marketplace(
        source: str = typer.Argument(
            "default",
            help="Marketplace source spec or URL. Use `default` for the curated default source.",
        ),
        profile_id: str = typer.Option(
            "default",
            "--profile",
            help="Profile id used to mark already installed skills.",
        ),
        limit: int = typer.Option(50, "--limit", min=1, help="Maximum skills to return."),
    ) -> None:
        """List installable skills from one marketplace source."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            listing = asyncio.run(
                get_skill_marketplace_service(settings).list_source(
                    source=source,
                    limit=limit,
                    profile_id=normalized_profile_id,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, SkillMarketplaceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_marketplace_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "source": source,
                    "resolved_source": listing.source,
                    "profile": normalized_profile_id,
                    "source_stats": marketplace_source_stats_to_payload(listing.source_stats),
                    "skills": [
                        marketplace_list_item_to_payload(item)
                        for item in listing.items
                    ],
                },
                ensure_ascii=True,
            )
        )

    @marketplace_app.command("search")
    def search_marketplace(
        query: str = typer.Argument(..., help="Free-text query to match skill names and summaries."),
        source: str = typer.Option(
            "default",
            "--source",
            help="Marketplace source spec or URL. Use `default` for the curated default source.",
        ),
        profile_id: str = typer.Option(
            "default",
            "--profile",
            help="Profile id used to mark already installed skills.",
        ),
        limit: int = typer.Option(50, "--limit", min=1, help="Maximum skills to return."),
    ) -> None:
        """Search installable skills from one marketplace source."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            listing = asyncio.run(
                get_skill_marketplace_service(settings).search_source(
                    source=source,
                    query=query,
                    limit=limit,
                    profile_id=normalized_profile_id,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, SkillMarketplaceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_marketplace_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "source": source,
                    "resolved_source": listing.source,
                    "query": query,
                    "profile": normalized_profile_id,
                    "source_stats": marketplace_source_stats_to_payload(listing.source_stats),
                    "skills": [
                        marketplace_list_item_to_payload(item)
                        for item in listing.items
                    ],
                },
                ensure_ascii=True,
            )
        )

    @marketplace_app.command("install")
    def install_marketplace(
        source: str = typer.Argument(
            "default",
            help="Marketplace source spec or URL. Use `default` for the curated default source.",
        ),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        skill: str | None = typer.Option(None, "--skill", help="Specific skill name for repo sources."),
        target_name: str | None = typer.Option(
            None,
            "--target-name",
            help="Optional local install name override.",
        ),
        overwrite: bool = typer.Option(
            False,
            "--overwrite",
            help="Replace an existing profile-local skill with the same name.",
        ),
    ) -> None:
        """Install one marketplace skill into the selected profile."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_skill_marketplace_service(settings).install(
                    profile_id=normalized_profile_id,
                    source=source,
                    skill=skill,
                    target_name=target_name,
                    overwrite=overwrite,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, SkillMarketplaceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="skill_marketplace_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"skill": marketplace_install_record_to_payload(item)},
                ensure_ascii=True,
            )
        )
