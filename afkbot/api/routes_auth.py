"""Core UI authentication routes for AFKBOT web/plugin surfaces."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from afkbot.services.ui_auth import (
    clear_ui_auth_cookie,
    peek_ui_auth_retry_after,
    read_ui_auth_session,
    reset_ui_auth_failures,
    set_ui_auth_cookie,
    ui_auth_is_configured,
    ui_auth_runtime_payload,
    verify_ui_auth_password,
    record_ui_auth_failure,
)
from afkbot.settings import get_settings

router = APIRouter(tags=["auth"])


class UILoginRequest(BaseModel):
    """Validated UI login payload."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=4096)


@router.get("/v1/auth/session")
async def get_auth_session(request: Request) -> JSONResponse:
    """Return current UI auth session state for browser clients."""

    settings = get_settings()
    session = read_ui_auth_session(request, settings)
    payload = ui_auth_runtime_payload(settings)
    response = {
        "authenticated": session is not None,
        "session": None,
        "auth": payload,
    }
    if session is not None:
        response["session"] = {
            "username": session.username,
            "issued_at_ts": session.issued_at_ts,
            "expires_at_ts": session.expires_at_ts,
            "last_seen_ts": session.last_seen_ts,
        }
    return JSONResponse(response)


@router.post("/v1/auth/login")
async def post_auth_login(login: UILoginRequest, request: Request) -> JSONResponse:
    """Authenticate the configured UI operator and issue an HTTP-only cookie."""

    settings = get_settings()
    if not ui_auth_is_configured(settings):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "ok": False,
                "error_code": "ui_auth_not_configured",
                "reason": "UI auth is not configured on this AFKBOT runtime.",
            },
        )

    remote_host = _remote_host(request, settings)
    retry_after = await peek_ui_auth_retry_after(
        settings=settings,
        remote_host=remote_host,
        username=login.username,
    )
    if retry_after is not None:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
            content={
                "ok": False,
                "error_code": "ui_auth_rate_limited",
                "reason": f"Too many failed login attempts. Retry after {retry_after} seconds.",
            },
        )

    if (
        login.username.strip() != str(settings.ui_auth_username or "").strip()
        or not verify_ui_auth_password(login.password, settings.ui_auth_password_hash)
    ):
        retry_after = await record_ui_auth_failure(
            settings=settings,
            remote_host=remote_host,
            username=login.username,
        )
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers=headers,
            content={
                "ok": False,
                "error_code": "ui_auth_invalid_credentials",
                "reason": "Invalid username or password.",
            },
        )

    await reset_ui_auth_failures(remote_host=remote_host, username=login.username)
    response = JSONResponse(
        {
            "ok": True,
            "username": login.username.strip(),
        }
    )
    set_ui_auth_cookie(
        response,
        request,
        settings,
        username=login.username.strip(),
    )
    return response


@router.post("/v1/auth/logout")
async def post_auth_logout() -> JSONResponse:
    """Terminate the current authenticated UI session."""

    response = JSONResponse({"ok": True})
    clear_ui_auth_cookie(response)
    return response


@router.get("/auth/login", response_model=None)
async def get_auth_login_page(request: Request, next: str | None = None):
    """Render the minimal built-in login page for protected plugin surfaces."""

    settings = get_settings()
    next_path = _safe_next_path(next)
    session = read_ui_auth_session(request, settings)
    if session is not None:
        return RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)
    if not ui_auth_is_configured(settings):
        return HTMLResponse(
            "<html><body><h1>UI auth is not configured.</h1></body></html>",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AFKBOT Login</title>
    <style>
      :root {{
        color-scheme: dark;
        font-family: Inter, system-ui, sans-serif;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: radial-gradient(circle at top, #13263a 0%, #0b1018 55%, #070a10 100%);
        color: #eef4ff;
      }}
      .card {{
        width: min(420px, calc(100vw - 32px));
        padding: 28px;
        border-radius: 20px;
        border: 1px solid rgba(160, 185, 220, 0.18);
        background: rgba(12, 17, 26, 0.92);
        box-shadow: 0 32px 80px rgba(0, 0, 0, 0.35);
      }}
      h1 {{ margin: 0 0 8px; font-size: 28px; }}
      p {{ margin: 0 0 20px; color: rgba(220, 232, 255, 0.72); line-height: 1.5; }}
      label {{ display: block; margin: 0 0 8px; font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(220, 232, 255, 0.56); }}
      input {{
        width: 100%;
        box-sizing: border-box;
        margin-bottom: 16px;
        padding: 14px 16px;
        border-radius: 14px;
        border: 1px solid rgba(160, 185, 220, 0.18);
        background: rgba(255,255,255,0.04);
        color: inherit;
        outline: none;
      }}
      button {{
        width: 100%;
        padding: 14px 16px;
        border: 0;
        border-radius: 14px;
        background: linear-gradient(135deg, #0ea5e9, #06b6d4);
        color: #07111b;
        font-weight: 700;
        cursor: pointer;
      }}
      .error {{
        min-height: 20px;
        margin-top: 14px;
        color: #fca5a5;
        font-size: 14px;
      }}
    </style>
  </head>
  <body>
    <form class="card" id="login-form">
      <h1>AFKBOT Login</h1>
      <p>Sign in to access protected AFKBOT plugin interfaces and APIs.</p>
      <label for="username">Username</label>
      <input id="username" name="username" value={json.dumps(str(settings.ui_auth_username or ""))} autocomplete="username" required />
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required />
      <button type="submit">Sign In</button>
      <div class="error" id="error"></div>
    </form>
    <script>
      const nextPath = {json.dumps(next_path)};
      const form = document.getElementById("login-form");
      const errorBox = document.getElementById("error");
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        errorBox.textContent = "";
        const payload = {{
          username: document.getElementById("username").value,
          password: document.getElementById("password").value,
        }};
        const response = await fetch("/v1/auth/login", {{
          method: "POST",
          headers: {{"content-type": "application/json"}},
          credentials: "same-origin",
          body: JSON.stringify(payload),
        }});
        const data = await response.json().catch(() => ({{}}));
        if (!response.ok) {{
          errorBox.textContent = data.reason || "Login failed.";
          return;
        }}
        window.location.assign(nextPath);
      }});
    </script>
  </body>
</html>"""
    return HTMLResponse(html)


def _safe_next_path(next_path: str | None) -> str:
    normalized = str(next_path or "").strip()
    if not normalized.startswith("/") or normalized.startswith("//"):
        return "/"
    return normalized


def _remote_host(request: Request, settings: object) -> str | None:
    trust_proxy_headers = bool(getattr(settings, "ui_auth_trust_proxy_headers", False))
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            host = forwarded.split(",", 1)[0].strip()
            if host:
                return host
    client = request.client
    if client is None:
        return None
    return client.host


def login_redirect_url(next_path: str) -> str:
    """Return the login redirect URL for one target path."""

    return f"/auth/login?{urlencode({'next': _safe_next_path(next_path)})}"
