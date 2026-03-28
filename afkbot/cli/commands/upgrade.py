"""Post-update upgrade runner commands."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.settings import Settings, get_settings
from afkbot.services.upgrade import UpgradeApplyReport, UpgradeService


async def _apply_upgrades(settings: Settings) -> UpgradeApplyReport:
    """Run upgrade apply and shutdown on one event loop."""

    service = UpgradeService(settings)
    try:
        return await service.apply()
    finally:
        await service.shutdown()


def register(app: typer.Typer) -> None:
    """Register `afk upgrade` command group."""

    upgrade_app = typer.Typer(
        help="Apply one-shot persisted-state upgrades after code updates.",
        no_args_is_help=True,
    )
    app.add_typer(upgrade_app, name="upgrade")

    @upgrade_app.command("apply")
    def apply_upgrades(
        quiet: bool = typer.Option(
            False,
            "--quiet",
            help="Print JSON only.",
        ),
    ) -> None:
        """Apply idempotent post-update upgrades to runtime state."""

        settings = get_settings()
        report = asyncio.run(_apply_upgrades(settings))
        payload = {
            "ok": True,
            "changed": report.changed,
            "steps": [
                {
                    "name": step.name,
                    "changed": step.changed,
                    "details": step.details,
                }
                for step in report.steps
            ],
        }
        if quiet:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return

        typer.echo(json.dumps(payload, ensure_ascii=True, indent=2))
