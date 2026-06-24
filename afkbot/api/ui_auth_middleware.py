"""Middleware protecting plugin web and API surfaces with AFKBOT UI auth."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from afkbot.api.routes_auth import login_redirect_url
from afkbot.services.plugins.contracts import PluginAuthMount
from afkbot.services.ui_auth import (
    maybe_refresh_ui_auth_cookie,
    read_ui_auth_session,
    resolve_ui_auth_surface,
)
from afkbot.settings import Settings


class PluginUIAuthMiddleware(BaseHTTPMiddleware):
    """Protect plugin UI and API routes when AFKBOT UI auth is enabled."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        plugin_auth_mounts: tuple[PluginAuthMount, ...] = (),
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._plugin_auth_mounts = plugin_auth_mounts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        surface = resolve_ui_auth_surface(
            request.url.path,
            self._settings,
            plugin_auth_mounts=self._plugin_auth_mounts,
        )
        if not surface.protected:
            response = await call_next(request)
            _apply_plugin_web_cache_headers(
                response=response,
                path=request.url.path,
                plugin_auth_mounts=self._plugin_auth_mounts,
            )
            return response

        if not surface.auth_configured:
            if surface.api_request:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "ok": False,
                        "error_code": "ui_auth_not_configured",
                        "reason": (
                            "Operator authentication is required for this UI, but it is not "
                            "configured. Run `afk auth setup` on the server, then sign in again."
                        ),
                    },
                )
            return Response(
                content=(
                    "Operator authentication is required for this UI, but it is not configured. "
                    "Run `afk auth setup` on the server, then sign in again."
                ),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                media_type="text/plain",
            )

        session = read_ui_auth_session(request, self._settings)
        if session is None:
            if surface.api_request:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "ok": False,
                        "error_code": "ui_auth_required",
                        "reason": (
                            "Your AFKBOT UI session is missing or expired. Open the sign-in "
                            "page, authenticate as an operator, then retry this action."
                        ),
                    },
                )
            return RedirectResponse(
                url=login_redirect_url(_request_target(request)),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        response = await call_next(request)
        maybe_refresh_ui_auth_cookie(
            response=response,
            request=request,
            settings=self._settings,
            session=session,
        )
        _apply_plugin_web_cache_headers(
            response=response,
            path=request.url.path,
            plugin_auth_mounts=self._plugin_auth_mounts,
        )
        return response


def _request_target(request: Request) -> str:
    query = request.url.query
    if not query:
        return str(request.url.path)
    return f"{request.url.path}?{query}"


def _apply_plugin_web_cache_headers(
    *,
    response: Response,
    path: str,
    plugin_auth_mounts: tuple[PluginAuthMount, ...],
) -> None:
    """Prevent stale plugin UI shells/assets after plugin upgrades."""

    if not _is_plugin_web_path(path=path, plugin_auth_mounts=plugin_auth_mounts):
        return
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _is_plugin_web_path(
    *,
    path: str,
    plugin_auth_mounts: tuple[PluginAuthMount, ...],
) -> bool:
    normalized = str(path or "").strip() or "/"
    for mount in plugin_auth_mounts:
        prefix = str(mount.web_prefix or "").strip().rstrip("/")
        if not prefix:
            continue
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return False
