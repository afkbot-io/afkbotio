"""Top-level CLI commands for AFKBOT UI authentication."""

from __future__ import annotations

import json

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.presentation.setup_prompts import prompt_confirm
from afkbot.services.ui_auth import (
    disable_ui_auth,
    hash_ui_auth_password,
    ui_auth_runtime_payload,
    upsert_ui_auth,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register UI auth commands."""

    auth_app = typer.Typer(
        help="Configure operator authentication for protected AFKBOT web/plugin surfaces.",
        no_args_is_help=True,
    )

    @auth_app.command("setup")
    def setup(
        username: str | None = typer.Option(None, "--username", help="UI auth username."),
        password: str | None = typer.Option(
            None,
            "--password",
            help="UI auth password. Prefer interactive prompt to avoid shell history.",
        ),
        session_ttl_sec: int | None = typer.Option(
            None,
            "--session-ttl-sec",
            min=1,
            help="Absolute authenticated session lifetime in seconds.",
        ),
        idle_ttl_sec: int | None = typer.Option(
            None,
            "--idle-ttl-sec",
            min=1,
            help="Idle session timeout in seconds.",
        ),
        login_rate_limit_window_sec: int | None = typer.Option(
            None,
            "--login-rate-limit-window-sec",
            min=1,
            help="Window for counting failed login attempts in seconds.",
        ),
        login_rate_limit_max_attempts: int | None = typer.Option(
            None,
            "--login-rate-limit-max-attempts",
            min=1,
            help="Maximum failed login attempts allowed per rate-limit window.",
        ),
        lockout_sec: int | None = typer.Option(
            None,
            "--lockout-sec",
            min=1,
            help="Minimum lockout/retry-after applied after repeated failures.",
        ),
        protected_plugin_id: list[str] | None = typer.Option(
            None,
            "--protected-plugin-id",
            help="Plugin id to protect. Repeat the flag to protect multiple plugins.",
        ),
        trust_proxy_headers: bool | None = typer.Option(
            None,
            "--trust-proxy-headers/--no-trust-proxy-headers",
            help="Trust X-Forwarded-* headers when AFKBOT runs behind a reverse proxy.",
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Require complete non-interactive input and skip interactive prompts.",
        ),
    ) -> None:
        """Create or replace the operator UI auth configuration."""

        _run_setup(
            username=username,
            password=password,
            session_ttl_sec=session_ttl_sec,
            idle_ttl_sec=idle_ttl_sec,
            login_rate_limit_window_sec=login_rate_limit_window_sec,
            login_rate_limit_max_attempts=login_rate_limit_max_attempts,
            lockout_sec=lockout_sec,
            protected_plugin_ids=protected_plugin_id,
            trust_proxy_headers=trust_proxy_headers,
            yes=yes,
            preserve_password=False,
        )

    @auth_app.command("create")
    def create(
        username: str | None = typer.Option(None, "--username", help="UI auth username."),
        password: str | None = typer.Option(
            None,
            "--password",
            help="UI auth password. Prefer interactive prompt to avoid shell history.",
        ),
        session_ttl_sec: int | None = typer.Option(None, "--session-ttl-sec", min=1),
        idle_ttl_sec: int | None = typer.Option(None, "--idle-ttl-sec", min=1),
        login_rate_limit_window_sec: int | None = typer.Option(
            None, "--login-rate-limit-window-sec", min=1
        ),
        login_rate_limit_max_attempts: int | None = typer.Option(
            None, "--login-rate-limit-max-attempts", min=1
        ),
        lockout_sec: int | None = typer.Option(None, "--lockout-sec", min=1),
        protected_plugin_id: list[str] | None = typer.Option(None, "--protected-plugin-id"),
        trust_proxy_headers: bool | None = typer.Option(
            None, "--trust-proxy-headers/--no-trust-proxy-headers"
        ),
        yes: bool = typer.Option(False, "--yes"),
    ) -> None:
        """Alias for `afk auth setup`."""

        _run_setup(
            username=username,
            password=password,
            session_ttl_sec=session_ttl_sec,
            idle_ttl_sec=idle_ttl_sec,
            login_rate_limit_window_sec=login_rate_limit_window_sec,
            login_rate_limit_max_attempts=login_rate_limit_max_attempts,
            lockout_sec=lockout_sec,
            protected_plugin_ids=protected_plugin_id,
            trust_proxy_headers=trust_proxy_headers,
            yes=yes,
            preserve_password=False,
        )

    @auth_app.command("update")
    def update(
        username: str | None = typer.Option(None, "--username", help="Updated UI auth username."),
        session_ttl_sec: int | None = typer.Option(None, "--session-ttl-sec", min=1),
        idle_ttl_sec: int | None = typer.Option(None, "--idle-ttl-sec", min=1),
        login_rate_limit_window_sec: int | None = typer.Option(
            None, "--login-rate-limit-window-sec", min=1
        ),
        login_rate_limit_max_attempts: int | None = typer.Option(
            None, "--login-rate-limit-max-attempts", min=1
        ),
        lockout_sec: int | None = typer.Option(None, "--lockout-sec", min=1),
        protected_plugin_id: list[str] | None = typer.Option(None, "--protected-plugin-id"),
        trust_proxy_headers: bool | None = typer.Option(
            None, "--trust-proxy-headers/--no-trust-proxy-headers"
        ),
        yes: bool = typer.Option(False, "--yes"),
    ) -> None:
        """Update UI auth policy while keeping the existing password."""

        _run_setup(
            username=username,
            password=None,
            session_ttl_sec=session_ttl_sec,
            idle_ttl_sec=idle_ttl_sec,
            login_rate_limit_window_sec=login_rate_limit_window_sec,
            login_rate_limit_max_attempts=login_rate_limit_max_attempts,
            lockout_sec=lockout_sec,
            protected_plugin_ids=protected_plugin_id,
            trust_proxy_headers=trust_proxy_headers,
            yes=yes,
            preserve_password=True,
        )

    @auth_app.command("status")
    def status(json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON.")) -> None:
        """Show the current UI auth policy and secret presence."""

        payload = ui_auth_runtime_payload(get_settings())
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True, sort_keys=True))
            return
        typer.echo(
            "\n".join(
                (
                    f"mode: {payload['mode']}",
                    f"configured: {'yes' if payload['configured'] else 'no'}",
                    f"username: {payload['username'] or '-'}",
                    f"session_ttl_sec: {payload['session_ttl_sec']}",
                    f"idle_ttl_sec: {payload['idle_ttl_sec']}",
                    f"login_rate_limit_window_sec: {payload['login_rate_limit_window_sec']}",
                    f"login_rate_limit_max_attempts: {payload['login_rate_limit_max_attempts']}",
                    f"lockout_sec: {payload['lockout_sec']}",
                    "protected_plugin_ids: "
                    + (", ".join(payload["protected_plugin_ids"]) or "-"),
                    f"trust_proxy_headers: {'yes' if payload['trust_proxy_headers'] else 'no'}",
                    f"password_hash_present: {'yes' if payload['password_hash_present'] else 'no'}",
                    f"cookie_key_present: {'yes' if payload['cookie_key_present'] else 'no'}",
                )
            )
        )

    @auth_app.command("rotate-password")
    def rotate_password(
        password: str | None = typer.Option(
            None,
            "--password",
            help="New UI auth password. Prefer interactive prompt to avoid shell history.",
        ),
    ) -> None:
        """Rotate the UI auth password while keeping the current policy."""

        settings = get_settings()
        if settings.ui_auth_mode != "password" or not settings.ui_auth_password_hash:
            raise_usage_error("UI auth is not configured. Run `afk auth setup` first.")
        resolved_password = password or _prompt_password(confirm=True)
        _persist_ui_auth(
            username=str(settings.ui_auth_username or ""),
            password_hash=hash_ui_auth_password(resolved_password),
            session_ttl_sec=settings.ui_auth_session_ttl_sec,
            idle_ttl_sec=settings.ui_auth_idle_ttl_sec,
            login_rate_limit_window_sec=settings.ui_auth_login_rate_limit_window_sec,
            login_rate_limit_max_attempts=settings.ui_auth_login_rate_limit_max_attempts,
            lockout_sec=settings.ui_auth_lockout_sec,
            protected_plugin_ids=settings.ui_auth_protected_plugin_ids,
            trust_proxy_headers=settings.ui_auth_trust_proxy_headers,
        )
        typer.echo("auth: password rotated")
        typer.echo("restart required: run `afk service restart` if the daemon is already running")

    @auth_app.command("disable")
    def disable(yes: bool = typer.Option(False, "--yes", help="Disable without confirmation.")) -> None:
        """Disable UI auth and remove stored operator credentials."""

        settings = get_settings()
        if not yes:
            confirmed = prompt_confirm(
                question="Disable UI auth and remove the stored password hash?",
                title="AFKBOT Auth",
                default=False,
            )
            if not confirmed:
                raise typer.Exit(code=1)
        disable_ui_auth(settings)
        get_settings.cache_clear()
        typer.echo("auth: disabled")
        typer.echo("restart required: run `afk service restart` if the daemon is already running")

    app.add_typer(auth_app, name="auth")


def _run_setup(
    *,
    username: str | None,
    password: str | None,
    session_ttl_sec: int | None,
    idle_ttl_sec: int | None,
    login_rate_limit_window_sec: int | None,
    login_rate_limit_max_attempts: int | None,
    lockout_sec: int | None,
    protected_plugin_ids: list[str] | None,
    trust_proxy_headers: bool | None,
    yes: bool,
    preserve_password: bool,
) -> None:
    settings = get_settings()
    current_payload = ui_auth_runtime_payload(settings)

    if yes and username is None and not current_payload["username"]:
        raise_usage_error("`--username` is required with `--yes`.")
    resolved_username = (
        str(username).strip()
        if username is not None
        else _prompt_text(prompt="Username", default=str(current_payload["username"] or ""), required=True, yes=yes)
    )

    resolved_password_hash = str(settings.ui_auth_password_hash or "")
    if preserve_password:
        if settings.ui_auth_mode != "password" or not resolved_password_hash:
            raise_usage_error("UI auth is not configured. Run `afk auth setup` first.")
    else:
        if yes and not password:
            raise_usage_error("`--password` is required with `--yes`.")
        resolved_password = password or _prompt_password(confirm=True)
        resolved_password_hash = hash_ui_auth_password(resolved_password)

    resolved_session_ttl = _resolve_int(
        value=session_ttl_sec,
        default=int(current_payload["session_ttl_sec"]),
        prompt="Session TTL (seconds)",
        yes=yes,
        minimum=1,
    )
    resolved_idle_ttl = _resolve_int(
        value=idle_ttl_sec,
        default=int(current_payload["idle_ttl_sec"]),
        prompt="Idle TTL (seconds)",
        yes=yes,
        minimum=1,
    )
    if resolved_idle_ttl > resolved_session_ttl:
        raise_usage_error("Idle TTL must be less than or equal to session TTL.")
    resolved_window = _resolve_int(
        value=login_rate_limit_window_sec,
        default=int(current_payload["login_rate_limit_window_sec"]),
        prompt="Login rate-limit window (seconds)",
        yes=yes,
        minimum=1,
    )
    resolved_attempts = _resolve_int(
        value=login_rate_limit_max_attempts,
        default=int(current_payload["login_rate_limit_max_attempts"]),
        prompt="Login rate-limit max attempts",
        yes=yes,
        minimum=1,
    )
    resolved_lockout = _resolve_int(
        value=lockout_sec,
        default=int(current_payload["lockout_sec"]),
        prompt="Lockout / retry-after (seconds)",
        yes=yes,
        minimum=1,
    )
    resolved_plugin_ids = _resolve_plugin_ids(
        provided=protected_plugin_ids,
        default=list(current_payload["protected_plugin_ids"]) or ["afkbotui"],
        yes=yes,
    )
    resolved_trust_proxy_headers = (
        bool(trust_proxy_headers)
        if trust_proxy_headers is not None
        else _prompt_bool(
            question="Trust reverse-proxy headers for client IP and HTTPS detection?",
            default=bool(current_payload["trust_proxy_headers"]),
            yes=yes,
        )
    )

    _persist_ui_auth(
        username=resolved_username,
        password_hash=resolved_password_hash,
        session_ttl_sec=resolved_session_ttl,
        idle_ttl_sec=resolved_idle_ttl,
        login_rate_limit_window_sec=resolved_window,
        login_rate_limit_max_attempts=resolved_attempts,
        lockout_sec=resolved_lockout,
        protected_plugin_ids=tuple(resolved_plugin_ids),
        trust_proxy_headers=resolved_trust_proxy_headers,
    )
    typer.echo("auth: configured")
    typer.echo(f"protected plugins: {', '.join(resolved_plugin_ids)}")
    typer.echo("restart required: run `afk service restart` if the daemon is already running")


def _persist_ui_auth(
    *,
    username: str,
    password_hash: str,
    session_ttl_sec: int,
    idle_ttl_sec: int,
    login_rate_limit_window_sec: int,
    login_rate_limit_max_attempts: int,
    lockout_sec: int,
    protected_plugin_ids: tuple[str, ...],
    trust_proxy_headers: bool,
) -> None:
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username=username,
        password_hash=password_hash,
        session_ttl_sec=session_ttl_sec,
        idle_ttl_sec=idle_ttl_sec,
        login_rate_limit_window_sec=login_rate_limit_window_sec,
        login_rate_limit_max_attempts=login_rate_limit_max_attempts,
        lockout_sec=lockout_sec,
        protected_plugin_ids=protected_plugin_ids,
        trust_proxy_headers=trust_proxy_headers,
    )
    get_settings.cache_clear()


def _prompt_password(*, confirm: bool) -> str:
    while True:
        value = str(
            typer.prompt(
                "Password",
                hide_input=True,
                confirmation_prompt=confirm,
            )
        )
        if value.strip():
            return value
        typer.echo("Password cannot be empty.")


def _prompt_text(prompt: str, *, default: str, required: bool, yes: bool) -> str:
    if yes:
        if required and not default.strip():
            raise_usage_error(f"{prompt} is required with `--yes`.")
        return default.strip()
    while True:
        value = str(typer.prompt(prompt, default=default)).strip()
        if value or not required:
            return value
        typer.echo(f"{prompt} cannot be empty.")


def _resolve_int(*, value: int | None, default: int, prompt: str, yes: bool, minimum: int) -> int:
    if value is not None:
        return value
    if yes:
        return default
    while True:
        resolved = int(typer.prompt(prompt, default=default))
        if resolved >= minimum:
            return resolved
        typer.echo(f"{prompt} must be >= {minimum}.")


def _resolve_plugin_ids(*, provided: list[str] | None, default: list[str], yes: bool) -> list[str]:
    if provided:
        normalized = _normalize_plugin_ids(provided)
        if normalized:
            return normalized
        raise_usage_error("Protected plugin ids cannot be empty.")
    if yes:
        return _normalize_plugin_ids(default) or ["afkbotui"]
    raw = str(
        typer.prompt(
            "Protected plugin ids (comma-separated)",
            default=", ".join(default),
        )
    ).strip()
    normalized = _normalize_plugin_ids(raw.split(","))
    if not normalized:
        raise_usage_error("Protected plugin ids cannot be empty.")
    return normalized


def _normalize_plugin_ids(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        plugin_id = str(value).strip().lower()
        if not plugin_id or plugin_id in seen:
            continue
        seen.add(plugin_id)
        normalized.append(plugin_id)
    return normalized


def _prompt_bool(*, question: str, default: bool, yes: bool) -> bool:
    if yes:
        return default
    return prompt_confirm(
        question=question,
        title="AFKBOT Auth",
        default=default,
    )
