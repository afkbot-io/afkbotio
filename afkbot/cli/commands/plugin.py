"""Top-level CLI commands for embedded AFKBOT plugins."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from afkbot.cli.commands.runtime_assets_common import emit_structured_error
from afkbot.services.plugins import (
    PluginServiceError,
    get_plugin_service,
    scaffold_plugin,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register `afk plugin ...` commands."""

    plugin_app = typer.Typer(
        help="Install and manage embedded AFKBOT platform plugins.",
        no_args_is_help=True,
    )
    app.add_typer(plugin_app, name="plugin")

    @plugin_app.command("list")
    def list_plugins() -> None:
        """List installed plugins."""

        try:
            items = get_plugin_service(get_settings()).list_installed()
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"plugins": [item.model_dump(mode="json") for item in items]},
                ensure_ascii=True,
            )
        )

    @plugin_app.command("inspect")
    def inspect_plugin(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
    ) -> None:
        """Show one installed plugin record."""

        try:
            item = get_plugin_service(get_settings()).inspect(plugin_id=plugin_id)
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("config-get")
    def get_plugin_config(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
    ) -> None:
        """Show one plugin config payload and its storage paths."""

        try:
            item = get_plugin_service(get_settings()).get_config(plugin_id=plugin_id)
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin_config": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("config-set")
    def set_plugin_config(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
        config_json: str = typer.Argument(..., help="JSON object payload for plugin config."),
    ) -> None:
        """Replace one plugin config with the provided JSON object."""

        try:
            payload = json.loads(config_json)
            if not isinstance(payload, dict):
                raise ValueError("Plugin config payload must be a JSON object")
            item = get_plugin_service(get_settings()).set_config(plugin_id=plugin_id, config=payload)
        except ValueError as exc:
            emit_structured_error(
                PluginServiceError(error_code="plugin_config_invalid", reason=str(exc)),
                default_error_code="plugin_error",
            )
            raise typer.Exit(code=1) from None
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin_config": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("config-reset")
    def reset_plugin_config(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
    ) -> None:
        """Reset one plugin config to manifest defaults."""

        try:
            item = get_plugin_service(get_settings()).reset_config(plugin_id=plugin_id)
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin_config": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("install")
    def install_plugin(
        source: str = typer.Argument(
            ...,
            help="Plugin source: local path, github:owner/repo@ref, or GitHub URL.",
        ),
        enable: bool = typer.Option(
            True,
            "--enable/--disable",
            help="Enable immediately after install.",
        ),
        overwrite: bool = typer.Option(
            False,
            "--overwrite",
            help="Replace the same installed version when it already exists.",
        ),
    ) -> None:
        """Install one local plugin source directory."""

        try:
            item = get_plugin_service(get_settings()).install(
                source=str(source),
                enable=enable,
                overwrite=overwrite,
            )
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("scaffold")
    def scaffold(
        destination: str = typer.Argument(..., help="Target directory for the new plugin repo."),
        plugin_id: str = typer.Option(..., "--plugin-id", help="Embedded plugin id."),
        name: str = typer.Option(..., "--name", help="Human-readable plugin name."),
        version: str = typer.Option("0.1.0", "--version", help="Initial plugin version."),
        api_router: bool = typer.Option(
            True,
            "--api-router/--no-api-router",
            help="Generate an API router stub.",
        ),
        static_web: bool = typer.Option(
            True,
            "--static-web/--no-static-web",
            help="Generate a static web surface stub.",
        ),
        skills: bool = typer.Option(
            False,
            "--skills/--no-skills",
            help="Generate a plugin-provided skills directory.",
        ),
        tools: bool = typer.Option(
            False,
            "--tools/--no-tools",
            help="Mark the scaffold as planning to expose tool factories.",
        ),
        apps: bool = typer.Option(
            False,
            "--apps/--no-apps",
            help="Mark the scaffold as planning to expose app registrars.",
        ),
        lifecycle: bool = typer.Option(
            False,
            "--lifecycle/--no-lifecycle",
            help="Generate startup/shutdown hook stubs for plugins that run with the platform.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Allow scaffolding into a non-empty directory.",
        ),
    ) -> None:
        """Create a starter embedded plugin repository layout."""

        try:
            result = scaffold_plugin(
                destination=Path(destination),
                plugin_id=plugin_id,
                name=name,
                version=version,
                api_router=api_router,
                static_web=static_web,
                skills=skills,
                tools=tools,
                apps=apps,
                lifecycle=lifecycle,
                force=force,
            )
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "plugin_root": str(result.plugin_root),
                    "manifest_path": str(result.manifest_path),
                    "entrypoint_path": str(result.entrypoint_path),
                },
                ensure_ascii=True,
            )
        )

    @plugin_app.command("update")
    def update_plugin(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
        enable: bool | None = typer.Option(
            None,
            "--enable/--disable",
            help="Optionally override enabled state after update.",
        ),
    ) -> None:
        """Reinstall one plugin from its persisted source."""

        try:
            item = get_plugin_service(get_settings()).update(
                plugin_id=plugin_id,
                enable=enable,
            )
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("enable")
    def enable_plugin(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
    ) -> None:
        """Enable one installed plugin."""

        try:
            item = get_plugin_service(get_settings()).enable(plugin_id=plugin_id)
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("disable")
    def disable_plugin(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
    ) -> None:
        """Disable one installed plugin."""

        try:
            item = get_plugin_service(get_settings()).disable(plugin_id=plugin_id)
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin": item.model_dump(mode="json")}, ensure_ascii=True))

    @plugin_app.command("remove")
    def remove_plugin(
        plugin_id: str = typer.Argument(..., help="Installed plugin id."),
        purge_files: bool = typer.Option(
            False,
            "--purge-files",
            help="Delete installed plugin files from the runtime root.",
        ),
    ) -> None:
        """Remove one plugin from the install registry."""

        try:
            item = get_plugin_service(get_settings()).remove(
                plugin_id=plugin_id,
                purge_files=purge_files,
            )
        except PluginServiceError as exc:
            emit_structured_error(exc, default_error_code="plugin_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"plugin": item.model_dump(mode="json")}, ensure_ascii=True))
