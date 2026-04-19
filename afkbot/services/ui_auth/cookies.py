"""Encrypted cookie helpers for AFKBOT UI authentication."""

from __future__ import annotations

import json
from time import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request, Response

from afkbot.services.ui_auth.configuration import ensure_ui_auth_cookie_key
from afkbot.services.ui_auth.contracts import UIAuthSession
from afkbot.services.ui_auth.passwords import password_hash_fingerprint
from afkbot.settings import Settings


UI_AUTH_COOKIE_NAME = "afk_ui_session"
_COOKIE_REFRESH_INTERVAL_SEC = 60


def build_ui_auth_cookie(
    settings: Settings,
    *,
    username: str,
    issued_at_ts: int | None = None,
    expires_at_ts: int | None = None,
    last_seen_ts: int | None = None,
) -> str:
    """Build one encrypted stateless session cookie."""

    now = int(time())
    issued_at = int(issued_at_ts or now)
    expires_at = int(expires_at_ts or (issued_at + settings.ui_auth_session_ttl_sec))
    payload = {
        "u": username,
        "iat": issued_at,
        "exp": expires_at,
        "lst": int(last_seen_ts or now),
        "pf": password_hash_fingerprint(settings.ui_auth_password_hash),
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return _fernet(settings).encrypt(raw).decode("ascii")


def read_ui_auth_session(request: Request, settings: Settings) -> UIAuthSession | None:
    """Decode and validate the UI auth session cookie from one request."""

    raw_cookie = str(request.cookies.get(UI_AUTH_COOKIE_NAME) or "").strip()
    if not raw_cookie:
        return None
    try:
        decoded = _fernet(settings).decrypt(raw_cookie.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        username = str(payload["u"]).strip()
        issued_at_ts = int(payload["iat"])
        expires_at_ts = int(payload["exp"])
        last_seen_ts = int(payload["lst"])
        password_fingerprint = str(payload["pf"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    now = int(time())
    if not username or username != str(settings.ui_auth_username or "").strip():
        return None
    if expires_at_ts <= now:
        return None
    if last_seen_ts + settings.ui_auth_idle_ttl_sec <= now:
        return None
    if password_fingerprint != password_hash_fingerprint(settings.ui_auth_password_hash):
        return None
    return UIAuthSession(
        username=username,
        issued_at_ts=issued_at_ts,
        expires_at_ts=expires_at_ts,
        last_seen_ts=last_seen_ts,
        password_fingerprint=password_fingerprint,
    )


def maybe_refresh_ui_auth_cookie(
    *,
    response: Response,
    request: Request,
    settings: Settings,
    session: UIAuthSession | None,
) -> None:
    """Refresh the cookie timestamp when the authenticated session is active."""

    if session is None:
        return
    now = int(time())
    if now - session.last_seen_ts < _COOKIE_REFRESH_INTERVAL_SEC:
        return
    set_ui_auth_cookie(
        response,
        request,
        settings,
        username=session.username,
        issued_at_ts=session.issued_at_ts,
        expires_at_ts=session.expires_at_ts,
        last_seen_ts=now,
    )


def set_ui_auth_cookie(
    response: Response,
    request: Request,
    settings: Settings,
    *,
    username: str,
    issued_at_ts: int | None = None,
    expires_at_ts: int | None = None,
    last_seen_ts: int | None = None,
) -> None:
    """Attach one authenticated UI auth cookie to the response."""

    cookie_value = build_ui_auth_cookie(
        settings,
        username=username,
        issued_at_ts=issued_at_ts,
        expires_at_ts=expires_at_ts,
        last_seen_ts=last_seen_ts,
    )
    absolute_expires = int(expires_at_ts or (int(time()) + settings.ui_auth_session_ttl_sec))
    response.set_cookie(
        key=UI_AUTH_COOKIE_NAME,
        value=cookie_value,
        max_age=max(1, absolute_expires - int(time())),
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request=request, settings=settings),
        path="/",
    )


def clear_ui_auth_cookie(response: Response) -> None:
    """Remove the authenticated UI auth cookie."""

    response.delete_cookie(
        key=UI_AUTH_COOKIE_NAME,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _fernet(settings: Settings) -> Fernet:
    return Fernet(ensure_ui_auth_cookie_key(settings).encode("ascii"))


def _cookie_secure(*, request: Request, settings: Settings) -> bool:
    scheme = request.url.scheme.strip().lower()
    if settings.ui_auth_trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-proto")
        if forwarded:
            scheme = forwarded.split(",", 1)[0].strip().lower() or scheme
    return scheme == "https"
