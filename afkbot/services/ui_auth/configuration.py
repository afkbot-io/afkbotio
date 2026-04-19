"""Runtime config/secrets helpers for AFKBOT UI auth."""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet

from afkbot.services.setup.runtime_store import (
    read_runtime_config,
    read_runtime_secrets,
    write_runtime_config,
    write_runtime_secrets,
)
from afkbot.services.ui_auth.contracts import UIAuthState
from afkbot.settings import Settings


def read_ui_auth_state(settings: Settings) -> UIAuthState:
    """Build resolved UI auth state from effective settings."""

    return UIAuthState(
        mode=settings.ui_auth_mode,
        username=settings.ui_auth_username,
        session_ttl_sec=settings.ui_auth_session_ttl_sec,
        idle_ttl_sec=settings.ui_auth_idle_ttl_sec,
        login_rate_limit_window_sec=settings.ui_auth_login_rate_limit_window_sec,
        login_rate_limit_max_attempts=settings.ui_auth_login_rate_limit_max_attempts,
        lockout_sec=settings.ui_auth_lockout_sec,
        protected_plugin_ids=settings.ui_auth_protected_plugin_ids,
        trust_proxy_headers=settings.ui_auth_trust_proxy_headers,
        password_hash_present=bool(settings.ui_auth_password_hash),
        cookie_key_present=bool(settings.ui_auth_cookie_key),
    )


def ensure_ui_auth_cookie_key(settings: Settings) -> str:
    """Resolve or create the persistent Fernet key used for UI auth cookies."""

    configured = str(settings.ui_auth_cookie_key or "").strip()
    if configured:
        return configured

    runtime_secrets = dict(read_runtime_secrets(settings))
    existing = str(runtime_secrets.get("ui_auth_cookie_key") or "").strip()
    if existing:
        return existing

    generated = Fernet.generate_key().decode("ascii")
    runtime_secrets["ui_auth_cookie_key"] = generated
    write_runtime_secrets(settings, secrets=runtime_secrets)
    return generated


def upsert_ui_auth(
    settings: Settings,
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
    """Persist UI auth configuration and secrets into the runtime store."""

    runtime_config = dict(read_runtime_config(settings))
    runtime_config.update(
        {
            "ui_auth_mode": "password",
            "ui_auth_username": username,
            "ui_auth_session_ttl_sec": int(session_ttl_sec),
            "ui_auth_idle_ttl_sec": int(idle_ttl_sec),
            "ui_auth_login_rate_limit_window_sec": int(login_rate_limit_window_sec),
            "ui_auth_login_rate_limit_max_attempts": int(login_rate_limit_max_attempts),
            "ui_auth_lockout_sec": int(lockout_sec),
            "ui_auth_protected_plugin_ids": list(protected_plugin_ids),
            "ui_auth_trust_proxy_headers": bool(trust_proxy_headers),
        }
    )
    write_runtime_config(settings, config=runtime_config)

    runtime_secrets = dict(read_runtime_secrets(settings))
    runtime_secrets["ui_auth_password_hash"] = password_hash
    runtime_secrets["ui_auth_cookie_key"] = str(
        runtime_secrets.get("ui_auth_cookie_key") or Fernet.generate_key().decode("ascii")
    )
    write_runtime_secrets(settings, secrets=runtime_secrets)


def disable_ui_auth(settings: Settings) -> None:
    """Disable UI auth while preserving unrelated runtime config/secrets."""

    runtime_config = dict(read_runtime_config(settings))
    for key in (
        "ui_auth_mode",
        "ui_auth_username",
        "ui_auth_session_ttl_sec",
        "ui_auth_idle_ttl_sec",
        "ui_auth_login_rate_limit_window_sec",
        "ui_auth_login_rate_limit_max_attempts",
        "ui_auth_lockout_sec",
        "ui_auth_protected_plugin_ids",
        "ui_auth_trust_proxy_headers",
    ):
        runtime_config.pop(key, None)
    write_runtime_config(settings, config=runtime_config)

    runtime_secrets = dict(read_runtime_secrets(settings))
    runtime_secrets.pop("ui_auth_password_hash", None)
    runtime_secrets.pop("ui_auth_cookie_key", None)
    write_runtime_secrets(settings, secrets=runtime_secrets)


def ui_auth_runtime_payload(settings: Settings) -> dict[str, Any]:
    """Return a public-facing runtime summary for CLI/API status endpoints."""

    state = read_ui_auth_state(settings)
    return {
        "mode": state.mode,
        "username": state.username,
        "configured": state.configured,
        "session_ttl_sec": state.session_ttl_sec,
        "idle_ttl_sec": state.idle_ttl_sec,
        "login_rate_limit_window_sec": state.login_rate_limit_window_sec,
        "login_rate_limit_max_attempts": state.login_rate_limit_max_attempts,
        "lockout_sec": state.lockout_sec,
        "protected_plugin_ids": list(state.protected_plugin_ids),
        "trust_proxy_headers": state.trust_proxy_headers,
        "password_hash_present": state.password_hash_present,
        "cookie_key_present": state.cookie_key_present,
    }

