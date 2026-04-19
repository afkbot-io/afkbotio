"""Middleware protecting plugin web and API surfaces with AFKBOT UI auth."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from afkbot.api.routes_auth import login_redirect_url
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
        protected_web_plugin_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._protected_web_plugin_ids = protected_web_plugin_ids

    async def dispatch(self, request: Request, call_next) -> Response:
        surface = resolve_ui_auth_surface(
            request.url.path,
            self._settings,
            protected_web_plugin_ids=self._protected_web_plugin_ids,
        )
        if not surface.protected:
            return await call_next(request)

        session = read_ui_auth_session(request, self._settings)
        if session is None:
            if surface.api_request:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "ok": False,
                        "error_code": "ui_auth_required",
                        "reason": "Authentication is required for this plugin API surface.",
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
        return response


def _request_target(request: Request) -> str:
    query = request.url.query
    if not query:
        return str(request.url.path)
    return f"{request.url.path}?{query}"
