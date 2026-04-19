"""AFKBOT UI authentication services."""

from afkbot.services.ui_auth.configuration import (
    disable_ui_auth,
    ensure_ui_auth_cookie_key,
    read_ui_auth_state,
    ui_auth_runtime_payload,
    upsert_ui_auth,
)
from afkbot.services.ui_auth.cookies import (
    UI_AUTH_COOKIE_NAME,
    clear_ui_auth_cookie,
    maybe_refresh_ui_auth_cookie,
    read_ui_auth_session,
    set_ui_auth_cookie,
)
from afkbot.services.ui_auth.passwords import (
    hash_ui_auth_password,
    password_hash_fingerprint,
    verify_ui_auth_password,
)
from afkbot.services.ui_auth.policy import resolve_ui_auth_surface, ui_auth_is_configured
from afkbot.services.ui_auth.rate_limit import (
    peek_ui_auth_retry_after,
    record_ui_auth_failure,
    reset_ui_auth_failures,
    reset_ui_auth_rate_limits,
)

__all__ = [
    "UI_AUTH_COOKIE_NAME",
    "clear_ui_auth_cookie",
    "disable_ui_auth",
    "ensure_ui_auth_cookie_key",
    "hash_ui_auth_password",
    "maybe_refresh_ui_auth_cookie",
    "password_hash_fingerprint",
    "peek_ui_auth_retry_after",
    "read_ui_auth_session",
    "read_ui_auth_state",
    "record_ui_auth_failure",
    "reset_ui_auth_failures",
    "reset_ui_auth_rate_limits",
    "resolve_ui_auth_surface",
    "set_ui_auth_cookie",
    "ui_auth_is_configured",
    "ui_auth_runtime_payload",
    "upsert_ui_auth",
    "verify_ui_auth_password",
]
