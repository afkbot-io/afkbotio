"""Profile command group for profile-scoped runtime agents."""

from __future__ import annotations

import typer

from afkbot.cli.commands.profile_binding import register_binding
from afkbot.cli.commands.profile_bootstrap import register_bootstrap
from afkbot.cli.commands.profile_core import register_core


def register(app: typer.Typer) -> None:
    """Register profile command group in Typer app."""

    profile_app = typer.Typer(
        help="Manage profile-scoped runtime agents, prompts, and model configuration.",
        no_args_is_help=True,
    )
    register_core(profile_app)
    register_binding(profile_app)
    register_bootstrap(profile_app)
    app.add_typer(profile_app, name="profile")
