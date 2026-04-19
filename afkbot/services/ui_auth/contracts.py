"""Contracts for AFKBOT UI authentication state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UIAuthSession:
    """Validated authenticated UI session decoded from the session cookie."""

    username: str
    issued_at_ts: int
    expires_at_ts: int
    last_seen_ts: int
    password_fingerprint: str


@dataclass(frozen=True, slots=True)
class UIAuthState:
    """Resolved UI auth runtime state built from config and secrets."""

    mode: str
    username: str | None
    session_ttl_sec: int
    idle_ttl_sec: int
    login_rate_limit_window_sec: int
    login_rate_limit_max_attempts: int
    lockout_sec: int
    protected_plugin_ids: tuple[str, ...]
    trust_proxy_headers: bool
    password_hash_present: bool
    cookie_key_present: bool

    @property
    def configured(self) -> bool:
        """Return whether UI auth is active and has the minimum secret state."""

        return self.mode == "password" and bool(self.username) and self.password_hash_present

