"""FastAPI application factory for AFKBOT chat/API adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from afkbot.api.chat_routes.router import router as chat_router
from afkbot.api.routes_connect import router as connect_router
from afkbot.api.routes_health import router as health_router
from afkbot.services.agent_loop.api_runtime import (
    initialize_api_runtime,
    shutdown_api_runtime,
)
from afkbot.settings import get_settings


def create_app() -> FastAPI:
    """Build API app with chat routes and health probes."""

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await initialize_api_runtime(settings=get_settings())
        try:
            yield
        finally:
            await shutdown_api_runtime()

    app = FastAPI(title="AFKBOT API", version="1.0.3", lifespan=_lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    app.include_router(chat_router)
    app.include_router(connect_router)
    app.include_router(health_router)
    return app
