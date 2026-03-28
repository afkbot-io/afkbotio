"""Connect command issuing one-time desktop app pairing URL."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.connect_target import (
    build_cli_connect_runtime_overrides,
    resolve_cli_connect_target,
)
from afkbot.services.connect import ConnectServiceError, issue_connect_url, normalize_base_url
from afkbot.services.connect.helpers import is_loopback_base_url
from afkbot.services.connect.helpers import generate_claim_pin, normalize_claim_pin
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register connect command in Typer app."""

    @app.command("connect")
    def connect(
        host: str | None = typer.Option(
            None,
            "--host",
            help="AFKBOT API base URL. Public non-local hosts must use https. Default: saved public chat/api URL from install, then http://<runtime_host>:<runtime_port+1>.",
        ),
        profile: str = typer.Option(
            "default",
            "--profile",
            help="Runtime profile id that will own the desktop session.",
        ),
        session: str = typer.Option(
            "desktop-session",
            "--session",
            help="Session id that the desktop app will bind to after connect.",
        ),
        resolve_binding: bool = typer.Option(
            False,
            "--resolve-binding/--no-resolve-binding",
            help="Resolve effective profile/session via persisted channel binding rules.",
        ),
        require_binding_match: bool = typer.Option(
            False,
            "--require-binding-match/--allow-binding-fallback",
            help="Fail when binding mode is enabled but no persisted rule matches the provided selectors.",
        ),
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Normalized transport name used for binding resolution.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Optional transport account/bot id for binding resolution.",
        ),
        peer_id: str | None = typer.Option(
            None,
            "--peer-id",
            help="Optional chat/group/peer id for binding resolution.",
        ),
        thread_id: str | None = typer.Option(
            None,
            "--thread-id",
            help="Optional thread/topic id for binding resolution.",
        ),
        user_id: str | None = typer.Option(
            None,
            "--user-id",
            help="Optional user id for binding resolution.",
        ),
        ttl_sec: int = typer.Option(
            120,
            "--ttl-sec",
            min=30,
            max=3600,
            help="One-time connect token TTL in seconds.",
        ),
        claim_pin: str | None = typer.Option(
            None,
            "--claim-pin",
            help="Optional out-of-band pairing PIN/secret required by POST /v1/connect/claim.",
        ),
        generate_claim_pin_flag: bool = typer.Option(
            False,
            "--generate-claim-pin/--no-generate-claim-pin",
            help="Generate a short pairing PIN and require it during claim. Share it separately from the connect URL.",
        ),
        allow_diagnostics: bool = typer.Option(
            False,
            "--allow-diagnostics/--no-allow-diagnostics",
            help="Allow paired client tokens to read operator /v1/health diagnostics. Keep off for public chat clients.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Issue a short-lived connect URL for pairing the desktop app."""

        try:
            if claim_pin is not None and generate_claim_pin_flag:
                raise_usage_error("--claim-pin and --generate-claim-pin cannot be used together.")
            settings = get_settings()
            default_base_url = settings.public_chat_api_url or f"http://{settings.runtime_host}:{settings.runtime_port + 1}"
            base_url = normalize_base_url(host or default_base_url)
            resolved_claim_pin = normalize_claim_pin(claim_pin)
            if generate_claim_pin_flag:
                resolved_claim_pin = generate_claim_pin()
            target = resolve_cli_connect_target(
                settings=settings,
                profile_id=profile.strip() or "default",
                session_id=session.strip() or "desktop-session",
                resolve_binding=resolve_binding,
                require_binding_match=require_binding_match,
                transport=transport,
                account_id=account_id,
                peer_id=peer_id,
                thread_id=thread_id,
                user_id=user_id,
            )
            runtime_overrides = build_cli_connect_runtime_overrides(
                target=target,
                transport=transport,
                account_id=account_id,
                peer_id=peer_id,
                thread_id=thread_id,
                user_id=user_id,
            )
            result = asyncio.run(
                issue_connect_url(
                    profile_id=target.profile_id,
                    session_id=target.session_id,
                    base_url=base_url,
                    ttl_sec=ttl_sec,
                    allow_diagnostics=allow_diagnostics,
                    claim_pin=resolved_claim_pin,
                    context_overrides=runtime_overrides,
                )
            )
        except ConnectServiceError as exc:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error_code": exc.error_code,
                            "reason": exc.reason,
                        },
                        ensure_ascii=True,
                    )
                )
                raise typer.Exit(code=1) from None
            raise_usage_error(exc.reason)

        expires_at = result.expires_at.isoformat().replace("+00:00", "Z")
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "ok": True,
                        "connect_url": result.connect_url,
                        "expires_at": expires_at,
                        "profile_id": result.profile_id,
                        "session_id": result.session_id,
                        **({"claim_pin": result.claim_pin} if result.claim_pin is not None else {}),
                    },
                    ensure_ascii=True,
                )
            )
            return

        typer.echo(f"profile: {result.profile_id}")
        typer.echo(f"session: {result.session_id}")
        typer.echo(f"expires_at: {expires_at}")
        if is_loopback_base_url(result.base_url):
            typer.secho(
                "warning: this connect URL targets loopback and works only on the same device. "
                "For phones or other machines, start AFKBOT on a reachable interface and pass "
                "`afk connect --host http://<LAN-IP>:<port>` or configure `public_chat_api_url`.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        if result.claim_pin is not None:
            typer.echo(f"claim_pin: {result.claim_pin}")
            typer.echo("share claim_pin separately from the connect URL")
        typer.echo(result.connect_url)
