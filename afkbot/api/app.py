"""FastAPI application factory for AFKBOT chat/API adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from afkbot.api.chat_routes.router import router as chat_router
from afkbot.api.routes_connect import router as connect_router
from afkbot.api.routes_health import router as health_router
from afkbot.api.routes_plugins import router as plugins_router
from afkbot.services.agent_loop.api_runtime import (
    initialize_api_runtime,
    shutdown_api_runtime,
)
from afkbot.services.plugins import get_plugin_service
from afkbot.settings import get_settings


def create_app() -> FastAPI:
    """Build API app with chat routes and health probes."""

    settings = get_settings()
    plugin_runtime = get_plugin_service(settings).load_runtime_snapshot()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        plugin_runtime_started = False
        await initialize_api_runtime(settings=settings)
        try:
            await plugin_runtime.run_startup(settings=settings)
            plugin_runtime_started = True
            yield
        finally:
            try:
                if plugin_runtime_started:
                    await plugin_runtime.run_shutdown(settings=settings)
            finally:
                await shutdown_api_runtime()

    app = FastAPI(title="AFKBOT API", version="1.1.1", lifespan=_lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "afkbot-api"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    app.include_router(chat_router)
    app.include_router(connect_router)
    app.include_router(health_router)
    app.include_router(plugins_router)
    for router in plugin_runtime.routers:
        app.include_router(router)
    for mount in plugin_runtime.static_mounts:
        app.mount(
            mount.mount_path,
            StaticFiles(directory=str(mount.directory), html=True),
            name=mount.name,
        )
    return app
