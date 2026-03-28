"""CLI entrypoint for setup, runtime, and maintenance commands."""

from __future__ import annotations

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.bootstrap import register as register_bootstrap
from afkbot.cli.commands.browser import register as register_browser
from afkbot.cli.commands.channel import register as register_channel
from afkbot.cli.commands.chat import register as register_chat
from afkbot.cli.commands.connect import register as register_connect
from afkbot.cli.commands.credentials import register as register_credentials
from afkbot.cli.commands.doctor import register as register_doctor
from afkbot.cli.commands.setup import register as register_setup
from afkbot.cli.commands.memory import register as register_memory
from afkbot.cli.commands.mcp import register as register_mcp
from afkbot.cli.commands.automation import register as register_automation
from afkbot.cli.commands.profile import register as register_profile
from afkbot.cli.commands.skill import register as register_skill
from afkbot.cli.commands.start import register as register_start
from afkbot.cli.commands.subagent import register as register_subagent
from afkbot.cli.commands.uninstall import register as register_uninstall
from afkbot.cli.commands.update import register as register_update
from afkbot.cli.commands.upgrade import register as register_upgrade
from afkbot.services.setup.state import setup_is_complete
from afkbot.settings import get_settings

app = typer.Typer(
    help=(
        "AFKBOT command-line interface.\n\n"
        "Use `afk start` to run the full local stack, `afk chat` for interactive or one-shot "
        "chat turns, `afk doctor` to verify local readiness, `afk bootstrap` to edit global "
        "system-prompt files, `afk update` to refresh managed installs, `afk automation` to "
        "manage scheduled tasks, `afk channel` to operate external adapters, `afk memory` to "
        "inspect profile memory, `afk mcp` to manage profile-local MCP IDE integrations, "
        "`afk skill` and `afk subagent` to manage profile assets, and `afk browser install` "
        "to prepare browser automation runtime."
    ),
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
register_setup(app)
register_uninstall(app)
register_update(app)
register_bootstrap(app)
register_automation(app)
register_browser(app)
register_channel(app)
register_chat(app)
register_connect(app)
register_credentials(app)
register_doctor(app)
register_memory(app)
register_mcp(app)
register_profile(app)
register_skill(app)
register_start(app)
register_subagent(app)
register_upgrade(app)


@app.callback(invoke_without_command=True)
def _guard_setup(ctx: typer.Context) -> None:
    """Require successful setup before using runtime commands."""

    if ctx.resilient_parsing:
        return
    command = ctx.invoked_subcommand
    if command is None:
        return
    if command in {"setup", "uninstall", "update", "browser", "bootstrap", "upgrade", "mcp"}:
        return

    settings = get_settings()
    if settings.skip_setup_guard:
        return
    if setup_is_complete(settings):
        return

    raise_usage_error("Run 'afk setup' first.", code=1)


def run() -> None:
    """Execute Typer app."""

    app()


if __name__ == "__main__":
    run()
