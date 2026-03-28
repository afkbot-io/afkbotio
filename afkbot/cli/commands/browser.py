"""Browser runtime command group for Playwright setup and status."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
from typing import Literal

import typer

from afkbot.cli.commands.browser_support import (
    active_browser_cdp_url,
    browser_install_question,
    browser_install_wizard_enabled,
    collect_browser_install_wizard_updates,
    format_backend_text,
    format_install_text,
    format_status_text,
    headless_env_override_active,
    headless_label,
    install_payload,
    managed_runtime_payload,
    persist_runtime_config_updates,
    preview_runtime_config_updates,
    resolve_prompt_language,
    should_confirm_browser_install,
    start_managed_browser_runtime,
    status_payload,
    stop_managed_browser_runtime,
)
from afkbot.cli.presentation.activity_indicator import ActivityIndicator
from afkbot.cli.presentation.setup_prompts import msg, prompt_confirm
from afkbot.services.browser_runtime import (
    get_browser_runtime_status,
    install_browser_runtime,
)
from afkbot.services.browser_sessions import get_browser_session_manager
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register browser command group in Typer app."""

    browser_app = typer.Typer(
        help="Manage browser runtime/backends used by browser.control.",
        no_args_is_help=True,
    )
    app.add_typer(browser_app, name="browser")

    @browser_app.command("status")
    def status(
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Check whether browser automation runtime is ready in the current Python env."""

        settings = get_settings()
        result = get_browser_runtime_status(settings)
        if json_output:
            typer.echo(json.dumps(status_payload(result, settings=settings), ensure_ascii=True))
        else:
            typer.echo(format_status_text(result, settings=settings))
        if not result.ok:
            raise typer.Exit(code=1)

    @browser_app.command("install")
    def install(
        force: bool = typer.Option(
            False,
            "--force",
            help="Reinstall browser runtime even when current status is already ready.",
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Run browser runtime install without confirmation prompt.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Install the active browser runtime/backend for browser.control."""

        settings = get_settings()
        lang = resolve_prompt_language(settings)
        pending_updates: dict[str, object | None] = {}
        if not json_output and not yes and browser_install_wizard_enabled():
            pending_updates = collect_browser_install_wizard_updates(
                settings=settings,
                lang=lang,
            )
            settings = preview_runtime_config_updates(settings, **pending_updates)

        initial_status = get_browser_runtime_status(settings)
        if not json_output and not yes and should_confirm_browser_install(force=force, status=initial_status):
            proceed = prompt_confirm(
                question=browser_install_question(
                    force=force,
                    status=initial_status,
                    settings=settings,
                    lang=lang,
                ),
                title=msg(
                    lang,
                    en="Browser: Install runtime",
                    ru="Браузер: Установка runtime",
                ),
                default=False,
                lang=lang,
            )
            if not proceed:
                typer.echo(
                    msg(
                        lang,
                        en="Browser install cancelled.",
                        ru="Установка браузерного runtime отменена.",
                )
                )
                raise typer.Exit(code=0)

        if pending_updates:
            settings, _ = persist_runtime_config_updates(settings, **pending_updates)

        activity = (
            nullcontext()
            if json_output
            else ActivityIndicator(label="Installing browser runtime")
        )
        with activity:
            result = install_browser_runtime(force=force, settings=settings)
        if json_output:
            typer.echo(json.dumps(install_payload(result, settings=settings), ensure_ascii=True))
        else:
            typer.echo(format_install_text(result, settings=settings))
        if not result.ok:
            raise typer.Exit(code=1)

    @browser_app.command("backend")
    def backend(
        value: Literal["status", "playwright_chromium", "lightpanda_cdp"] = typer.Argument(
            "status",
            help="Show current browser backend or persist a new one.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Show or update persisted browser backend for browser.control."""

        settings = get_settings()
        changed = False
        current_backend = settings.browser_backend

        if value != "status":
            settings, changed = persist_runtime_config_updates(
                settings,
                browser_backend=value,
            )
            current_backend = settings.browser_backend

        payload = {
            "ok": True,
            "browser_backend": current_backend,
            "changed": changed,
            "browser_cdp_url": active_browser_cdp_url(settings),
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"browser backend: {format_backend_text(settings)}")

    @browser_app.command("cdp-url")
    def cdp_url(
        value: str = typer.Argument(
            "status",
            help="Show, update, or clear the configured browser CDP endpoint URL.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Show or update persisted browser CDP URL used by CDP-based backends."""

        settings = get_settings()
        changed = False
        current_value = settings.browser_cdp_url

        if value != "status":
            desired_value = None if value == "clear" else value.strip()
            settings, changed = persist_runtime_config_updates(
                settings,
                browser_cdp_url=desired_value,
            )
            current_value = settings.browser_cdp_url

        payload = {
            "ok": True,
            "browser_cdp_url": current_value,
            "changed": changed,
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"browser cdp url: {current_value or '(not set)'}")

    @browser_app.command("headless")
    def headless(
        mode: Literal["status", "on", "off"] = typer.Argument(
            "status",
            help="Show current mode or persist browser headed/headless mode.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Show or update persisted headed/headless mode for browser.control."""

        settings = get_settings()
        env_override_active = headless_env_override_active()
        current_mode = settings.browser_headless
        changed = False

        if mode != "status":
            desired_mode = mode == "on"
            settings, changed = persist_runtime_config_updates(
                settings,
                browser_headless=desired_mode,
            )
            current_mode = settings.browser_headless

        payload = {
            "ok": True,
            "browser_headless": current_mode,
            "changed": changed,
            "env_override_active": env_override_active,
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return

        suffix = (
            " (AFKBOT_BROWSER_HEADLESS env override is active)"
            if env_override_active
            else ""
        )
        typer.echo(f"browser headless: {headless_label(current_mode)}{suffix}")

    @browser_app.command("start")
    def start(
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Start the managed browser runtime for the active backend, when supported."""

        settings = get_settings()
        result = start_managed_browser_runtime(settings)
        if json_output:
            typer.echo(json.dumps(managed_runtime_payload(result, settings=settings), ensure_ascii=True))
        else:
            typer.echo(result.reason)
        if not result.ok:
            raise typer.Exit(code=1)

    @browser_app.command("stop")
    def stop(
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Stop the managed browser runtime for the active backend, when supported."""

        settings = get_settings()
        result = stop_managed_browser_runtime(settings)
        if json_output:
            typer.echo(json.dumps(managed_runtime_payload(result, settings=settings), ensure_ascii=True))
        else:
            typer.echo(result.reason)
        if not result.ok:
            raise typer.Exit(code=1)

    @browser_app.command("close")
    def close(
        profile: str = typer.Option(
            "default",
            "--profile",
            help="Profile id that owns the browser session.",
        ),
        session: str = typer.Option(
            "cli-session",
            "--session",
            help="Chat session id whose browser should be closed.",
        ),
        all_sessions: bool = typer.Option(
            False,
            "--all",
            help="Close every live browser session for current workspace root.",
        ),
        clear_state: bool = typer.Option(
            False,
            "--clear-state",
            help="Also delete persisted browser storage state for the target session(s).",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Close one persisted in-process browser session or all workspace sessions."""

        settings = get_settings()
        manager = get_browser_session_manager()
        if all_sessions:
            closed = asyncio.run(
                manager.close_all_for_root(
                    root_dir=settings.root_dir,
                    clear_persisted_state=clear_state,
                )
            )
            payload = {
                "ok": True,
                "closed_count": closed,
                "scope": "all",
                "clear_state": clear_state,
            }
        else:
            closed = asyncio.run(
                manager.close_session(
                    root_dir=settings.root_dir,
                    profile_id=profile,
                    session_id=session,
                    clear_persisted_state=clear_state,
                )
            )
            payload = {
                "ok": True,
                "closed": closed,
                "scope": "session",
                "profile_id": profile,
                "session_id": session,
                "clear_state": clear_state,
            }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        if all_sessions:
            suffix = " and persisted state cleared" if clear_state else ""
            typer.echo(f"browser sessions closed: {closed}{suffix}")
            return
        if closed:
            suffix = " (persisted state cleared)" if clear_state else ""
            typer.echo(f"browser session closed: {profile}/{session}{suffix}")
            return
        if clear_state:
            typer.echo(f"browser session was not open: {profile}/{session} (persisted state cleared if present)")
            return
        typer.echo(f"browser session was not open: {profile}/{session}")
