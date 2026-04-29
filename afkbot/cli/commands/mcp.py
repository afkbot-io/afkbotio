"""CLI commands for profile-scoped MCP IDE integration configs."""

from __future__ import annotations

import asyncio
import json
from typing import NoReturn

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.runtime_assets_common import emit_command_error
from afkbot.cli.presentation.mcp_wizard import (
    confirm_mcp_add,
    confirm_mcp_remove,
    mcp_wizard_enabled,
    prompt_mcp_capabilities,
    prompt_mcp_server,
    prompt_mcp_transport,
    prompt_optional_refs,
    render_mcp_add_preview,
    render_mcp_remove_preview,
    prompt_resolved_mcp_url,
)
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, resolve_prompt_language
from afkbot.services.mcp_integration.operator_contracts import (
    MCPAddResult,
    MCPServerView,
    MCP_CONFIG_BOUNDARY_NOTE,
)
from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.mcp_integration.url_resolver import resolve_mcp_url
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register `afk mcp ...` commands."""

    mcp_app = typer.Typer(
        help="Manage profile-local MCP IDE integration configs.",
        no_args_is_help=True,
    )
    app.add_typer(mcp_app, name="mcp")

    def _emit_server_view(item: MCPServerView) -> None:
        typer.echo(f"- server: {item.server}")
        typer.echo(f"- transport: {item.transport}")
        if item.url:
            typer.echo(f"- url: {item.url}")
        typer.echo(f"- capabilities: {', '.join(item.capabilities) or 'none'}")
        typer.echo(f"- env_refs: {', '.join(item.env_refs) or 'none'}")
        typer.echo(f"- secret_refs: {', '.join(item.secret_refs) or 'none'}")
        typer.echo(f"- enabled: {item.enabled}")
        typer.echo(f"- config_source: {item.config_source or '-'}")
        typer.echo(f"- runtime_available: {item.access.runtime_available}")
        typer.echo(f"- ide_visible: {item.access.ide_visible}")
        typer.echo(f"- boundary: {item.access.boundary_note}")

    def _emit_add_or_update_result(*, label: str, result: MCPAddResult) -> None:
        typer.echo(f"{label}: {result.server.server}")
        _emit_server_view(result.server)
        typer.echo(f"- storage_mode: {result.storage_mode}")
        typer.echo(f"- target_path: {result.target_path}")

    def _execute_add_mcp(
        *,
        profile_id: str,
        url: str | None,
        missing_url_reason: str,
        server: str | None,
        transport: str | None,
        capability: list[str] | None,
        env_ref: list[str] | None,
        secret_ref: list[str] | None,
        enabled: bool,
        yes: bool,
        json_output: bool,
    ) -> None:
        settings = get_settings()
        prompt_language = _resolve_mcp_prompt_language(settings)
        interactive = mcp_wizard_enabled() and not json_output
        resolved_url = (url or "").strip()
        if not resolved_url:
            if not interactive:
                _exit_mcp_usage_error(
                    missing_url_reason,
                    json_output=json_output,
                )
            resolution = prompt_resolved_mcp_url(lang=prompt_language)
        else:
            try:
                resolution = resolve_mcp_url(resolved_url)
            except ValueError as exc:
                _exit_mcp_error(exc, json_output=json_output)

        resolved_server = (server or "").strip() or resolution.suggested_server
        resolved_transport = (transport or "").strip().lower() or resolution.suggested_transport
        resolved_capabilities = tuple(capability or ("tools",))
        resolved_env_refs = tuple(env_ref or ())
        resolved_secret_refs = tuple(secret_ref or ())

        if interactive and not yes:
            if server is None:
                resolved_server = prompt_mcp_server(default=resolved_server, lang=prompt_language)
            if transport is None:
                resolved_transport = prompt_mcp_transport(default=resolved_transport, lang=prompt_language)
            if capability is None:
                resolved_capabilities = prompt_mcp_capabilities(defaults=resolved_capabilities, lang=prompt_language)
            if env_ref is None:
                resolved_env_refs = prompt_optional_refs(
                    label=msg(prompt_language, en="Environment refs", ru="Ссылки на environment"),
                    suggestion=resolution.suggested_env_ref,
                    default_values=resolved_env_refs,
                    lang=prompt_language,
                )
            if secret_ref is None:
                resolved_secret_refs = prompt_optional_refs(
                    label=msg(prompt_language, en="Secret refs", ru="Ссылки на secrets"),
                    suggestion=resolution.suggested_secret_ref,
                    default_values=resolved_secret_refs,
                    lang=prompt_language,
                )

        try:
            service = get_mcp_profile_service(settings)
            preview = asyncio.run(
                service.preview_add_by_url(
                    profile_id=profile_id,
                    url=resolution.url,
                    server=resolved_server,
                    transport=resolved_transport,
                    capabilities=resolved_capabilities,
                    env_refs=resolved_env_refs,
                    secret_refs=resolved_secret_refs,
                    enabled=enabled,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if interactive and not yes:
            preview_text = render_mcp_add_preview(
                resolution=resolution,
                preview_server_id=preview.server.server,
                preview_transport=preview.server.transport,
                preview_capabilities=preview.server.capabilities,
                preview_env_refs=preview.server.env_refs,
                preview_secret_refs=preview.server.secret_refs,
                target_path=preview.target_path,
                storage_mode=preview.storage_mode,
                replacing_existing=preview.would_replace_effective_server,
                enabled=preview.server.enabled,
                lang=prompt_language,
            )
            if not confirm_mcp_add(preview_text=preview_text, lang=prompt_language):
                raise typer.Exit(code=0)

        try:
            result = asyncio.run(
                service.add_by_url(
                    profile_id=profile_id,
                    url=resolution.url,
                    server=resolved_server,
                    transport=resolved_transport,
                    capabilities=resolved_capabilities,
                    env_refs=resolved_env_refs,
                    secret_refs=resolved_secret_refs,
                    enabled=enabled,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if json_output:
            typer.echo(json.dumps({"result": result.model_dump(mode="json")}, ensure_ascii=True))
            return
        _emit_add_or_update_result(
            label=f"MCP server {'created' if result.created else 'updated'}",
            result=result,
        )

    @mcp_app.command("list")
    def list_mcp(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
        show_disabled: bool = typer.Option(
            False,
            "--show-disabled",
            help="Include disabled MCP servers in the result.",
        ),
    ) -> None:
        """List effective MCP servers configured for one profile."""

        try:
            settings = get_settings()
            items = asyncio.run(
                get_mcp_profile_service(settings).list(
                    profile_id=profile_id,
                    show_disabled=show_disabled,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if json_output:
            typer.echo(
                json.dumps(
                    {"servers": [item.model_dump(mode="json") for item in items]},
                    ensure_ascii=True,
                )
            )
            return
        if not items:
            if show_disabled:
                typer.echo(f"No MCP servers configured for profile `{profile_id}`.")
            else:
                typer.echo(
                    f"No enabled MCP servers configured for profile `{profile_id}`. "
                    "Use `afk mcp list --show-disabled` to include disabled entries."
                )
            typer.echo(
                f"- boundary: {MCP_CONFIG_BOUNDARY_NOTE}"
            )
            return
        for item in items:
            typer.echo(
                f"- {item.server}: transport={item.transport}, enabled={item.enabled}, "
                f"capabilities={','.join(item.capabilities) or 'none'}, "
                f"source={item.config_source or '-'}"
            )
            typer.echo(
                f"  access_inputs: env_refs={','.join(item.env_refs) or 'none'}, "
                f"secret_refs={','.join(item.secret_refs) or 'none'}"
            )
            if item.url:
                typer.echo(f"  url: {item.url}")
            typer.echo(f"  boundary: {item.access.boundary_note}")

    @mcp_app.command("get")
    def get_mcp(
        server: str = typer.Argument(..., metavar="SERVER"),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show one effective MCP server config for the selected profile."""

        try:
            settings = get_settings()
            item = asyncio.run(get_mcp_profile_service(settings).get(profile_id=profile_id, server=server))
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if json_output:
            typer.echo(json.dumps({"server": item.model_dump(mode="json")}, ensure_ascii=True))
            return
        typer.echo(f"MCP server: {item.server}")
        _emit_server_view(item)

    @mcp_app.command("validate")
    def validate_mcp(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Validate profile-local MCP config files and print a structured report."""

        try:
            settings = get_settings()
            report = asyncio.run(get_mcp_profile_service(settings).validate(profile_id=profile_id))
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if json_output:
            typer.echo(json.dumps({"report": report.model_dump(mode="json")}, ensure_ascii=True))
            return
        typer.echo(f"MCP validate: {'ok' if report.ok else 'failed'}")
        typer.echo(f"- profile: {report.profile_id}")
        typer.echo(f"- storage_mode: {report.storage_mode}")
        typer.echo(f"- files_checked: {len(report.files_checked)}")
        if report.files_checked:
            for item in report.files_checked:
                typer.echo(f"  - {item}")
        typer.echo(f"- effective_servers: {len(report.servers)}")
        for note in report.notes:
            typer.echo(f"- note: {note}")
        for error in report.errors:
            typer.echo(f"- error: {error}")
        if not report.ok:
            raise typer.Exit(code=1)

    @mcp_app.command("add")
    def add_mcp(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        url: str | None = typer.Option(None, "--url", help="Remote MCP endpoint URL."),
        server: str | None = typer.Option(None, "--server", help="Normalized MCP server id."),
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Remote transport: http, sse, or websocket.",
        ),
        capability: list[str] | None = typer.Option(
            None,
            "--capability",
            help="Advertised capability. Repeat for multiple values.",
        ),
        env_ref: list[str] | None = typer.Option(
            None,
            "--env-ref",
            help="Required environment ref. Repeat for multiple values.",
        ),
        secret_ref: list[str] | None = typer.Option(
            None,
            "--secret-ref",
            help="Required secret ref. Repeat for multiple values.",
        ),
        enabled: bool = typer.Option(
            True,
            "--enabled/--disabled",
            help="Store the MCP server as enabled or disabled.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the interactive preview confirmation."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Add or update one remote MCP server config for the current profile."""

        _execute_add_mcp(
            profile_id=profile_id,
            url=url,
            missing_url_reason="--url is required without an interactive TTY",
            server=server,
            transport=transport,
            capability=capability,
            env_ref=env_ref,
            secret_ref=secret_ref,
            enabled=enabled,
            yes=yes,
            json_output=json_output,
        )

    @mcp_app.command("connect")
    def connect_mcp(
        mcp_url: str | None = typer.Argument(None, metavar="URL"),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        server: str | None = typer.Option(None, "--server", help="Normalized MCP server id."),
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Remote transport: http, sse, or websocket.",
        ),
        capability: list[str] | None = typer.Option(
            None,
            "--capability",
            help="Advertised capability. Repeat for multiple values.",
        ),
        env_ref: list[str] | None = typer.Option(
            None,
            "--env-ref",
            help="Required environment ref. Repeat for multiple values.",
        ),
        secret_ref: list[str] | None = typer.Option(
            None,
            "--secret-ref",
            help="Required secret ref. Repeat for multiple values.",
        ),
        enabled: bool = typer.Option(
            True,
            "--enabled/--disabled",
            help="Store the MCP server as enabled or disabled.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the interactive preview confirmation."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Connect one remote MCP endpoint URL to the current profile."""

        _execute_add_mcp(
            profile_id=profile_id,
            url=mcp_url,
            missing_url_reason="URL argument is required without an interactive TTY",
            server=server,
            transport=transport,
            capability=capability,
            env_ref=env_ref,
            secret_ref=secret_ref,
            enabled=enabled,
            yes=yes,
            json_output=json_output,
        )

    @mcp_app.command("edit")
    def edit_mcp(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        server: str = typer.Option(..., "--server", help="Normalized MCP server id to update."),
        url: str | None = typer.Option(None, "--url", help="Remote MCP endpoint URL override."),
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Remote transport override: http, sse, or websocket.",
        ),
        capability: list[str] | None = typer.Option(
            None,
            "--capability",
            help="Advertised capability override. Repeat for multiple values.",
        ),
        env_ref: list[str] | None = typer.Option(
            None,
            "--env-ref",
            help="Required environment ref override. Repeat for multiple values.",
        ),
        secret_ref: list[str] | None = typer.Option(
            None,
            "--secret-ref",
            help="Required secret ref override. Repeat for multiple values.",
        ),
        enabled: bool | None = typer.Option(
            None,
            "--enabled/--disabled",
            help="Override whether the MCP server is enabled.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the interactive preview confirmation."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Edit one existing remote MCP server config entry."""

        interactive = mcp_wizard_enabled() and not json_output
        try:
            settings = get_settings()
            prompt_language = _resolve_mcp_prompt_language(settings)
            service = get_mcp_profile_service(settings)
            current = asyncio.run(service.get(profile_id=profile_id, server=server))
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        current_url = (current.url or "").strip()
        resolved_url = (url or "").strip()
        if not resolved_url:
            if current_url:
                try:
                    resolution = resolve_mcp_url(current_url)
                except ValueError as exc:
                    _exit_mcp_error(exc, json_output=json_output)
            elif not interactive:
                _exit_mcp_usage_error(
                    "--url is required when the current MCP config has no remote URL",
                    json_output=json_output,
                )
            else:
                resolution = prompt_resolved_mcp_url(lang=prompt_language)
        else:
            try:
                resolution = resolve_mcp_url(resolved_url)
            except ValueError as exc:
                _exit_mcp_error(exc, json_output=json_output)

        resolved_transport = (transport or "").strip().lower() or current.transport
        resolved_capabilities = tuple(capability or current.capabilities)
        resolved_env_refs = tuple(env_ref or current.env_refs)
        resolved_secret_refs = tuple(secret_ref or current.secret_refs)
        resolved_enabled = current.enabled if enabled is None else enabled

        if interactive and not yes:
            if transport is None:
                resolved_transport = prompt_mcp_transport(default=resolved_transport, lang=prompt_language)
            if capability is None:
                resolved_capabilities = prompt_mcp_capabilities(defaults=resolved_capabilities, lang=prompt_language)
            if env_ref is None:
                resolved_env_refs = prompt_optional_refs(
                    label=msg(prompt_language, en="Environment refs", ru="Ссылки на environment"),
                    suggestion=resolution.suggested_env_ref,
                    default_values=current.env_refs,
                    lang=prompt_language,
                )
            if secret_ref is None:
                resolved_secret_refs = prompt_optional_refs(
                    label=msg(prompt_language, en="Secret refs", ru="Ссылки на secrets"),
                    suggestion=resolution.suggested_secret_ref,
                    default_values=current.secret_refs,
                    lang=prompt_language,
                )

        try:
            preview = asyncio.run(
                service.preview_add_by_url(
                    profile_id=profile_id,
                    url=resolution.url,
                    server=current.server,
                    transport=resolved_transport,
                    capabilities=resolved_capabilities,
                    env_refs=resolved_env_refs,
                    secret_refs=resolved_secret_refs,
                    enabled=resolved_enabled,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if interactive and not yes:
            preview_text = render_mcp_add_preview(
                resolution=resolution,
                preview_server_id=preview.server.server,
                preview_transport=preview.server.transport,
                preview_capabilities=preview.server.capabilities,
                preview_env_refs=preview.server.env_refs,
                preview_secret_refs=preview.server.secret_refs,
                target_path=preview.target_path,
                storage_mode=preview.storage_mode,
                replacing_existing=True,
                enabled=preview.server.enabled,
                lang=prompt_language,
            )
            if not confirm_mcp_add(preview_text=preview_text, lang=prompt_language):
                raise typer.Exit(code=0)

        try:
            result = asyncio.run(
                service.add_by_url(
                    profile_id=profile_id,
                    url=resolution.url,
                    server=current.server,
                    transport=resolved_transport,
                    capabilities=resolved_capabilities,
                    env_refs=resolved_env_refs,
                    secret_refs=resolved_secret_refs,
                    enabled=resolved_enabled,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if json_output:
            typer.echo(json.dumps({"result": result.model_dump(mode="json")}, ensure_ascii=True))
            return
        _emit_add_or_update_result(label="MCP server updated", result=result)

    @mcp_app.command("remove")
    def remove_mcp(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        server: str = typer.Option(..., "--server", help="Normalized MCP server id to remove."),
        yes: bool = typer.Option(False, "--yes", help="Skip the interactive confirmation."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Remove one operator-managed MCP server config entry."""

        interactive = mcp_wizard_enabled() and not json_output
        try:
            settings = get_settings()
            prompt_language = _resolve_mcp_prompt_language(settings)
            service = get_mcp_profile_service(settings)
            preview = asyncio.run(
                service.preview_remove(
                    profile_id=profile_id,
                    server=server,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if interactive and not yes:
            preview_text = render_mcp_remove_preview(
                server=preview.server.server,
                transport=preview.server.transport,
                url=preview.server.url,
                target_path=preview.target_path,
                storage_mode=preview.storage_mode,
                config_source=preview.server.config_source,
                lang=prompt_language,
            )
            if not confirm_mcp_remove(preview_text=preview_text, lang=prompt_language):
                raise typer.Exit(code=0)

        try:
            result = asyncio.run(
                service.remove(
                    profile_id=profile_id,
                    server=server,
                )
            )
        except (MCPIntegrationError, ProfileServiceError, ValueError) as exc:
            _exit_mcp_error(exc, json_output=json_output)

        if json_output:
            typer.echo(json.dumps({"result": result.model_dump(mode="json")}, ensure_ascii=True))
            return
        typer.echo(f"MCP server removed: {result.removed_server}")
        typer.echo(f"- storage_mode: {result.storage_mode}")
        typer.echo(f"- target_path: {result.target_path}")
        typer.echo(f"- boundary: {MCP_CONFIG_BOUNDARY_NOTE}")


class _MCPCLIError(ValueError):
    """Synthetic CLI error used to render deterministic MCP command failures."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def _resolve_mcp_prompt_language(settings: object) -> PromptLanguage:
    """Resolve MCP wizard language without making test doubles implement Settings internals."""

    try:
        return resolve_prompt_language(settings=settings, value=None, ru=False)  # type: ignore[arg-type]
    except (AttributeError, OSError, ValueError):
        return resolve_prompt_language(settings=None, value=None, ru=False)


def _exit_mcp_error(exc: Exception, *, json_output: bool) -> NoReturn:
    """Render one MCP command failure with the active output mode."""

    emit_command_error(exc, default_error_code="mcp_error", json_output=json_output)
    raise typer.Exit(code=1) from None


def _exit_mcp_usage_error(reason: str, *, json_output: bool) -> NoReturn:
    """Render one MCP usage error with the active output mode."""

    if not json_output:
        raise_usage_error(reason)
    emit_command_error(
        _MCPCLIError(error_code="usage_error", reason=reason),
        default_error_code="usage_error",
        json_output=True,
    )
    raise typer.Exit(code=2) from None
