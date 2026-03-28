"""Validation storage helpers for runtime daemon webhook ingress."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory
from afkbot.services.automations.runtime_daemon_http import WebhookTokenValidator
from afkbot.settings import Settings


async def prepare_validation_resources(
    *,
    settings: Settings,
    webhook_token_validator: WebhookTokenValidator | None,
) -> tuple[AsyncEngine | None, async_sessionmaker[AsyncSession] | None]:
    """Initialize DB resources needed for webhook token validation."""

    if webhook_token_validator is None:
        engine = create_engine(settings)
        await create_schema(engine)
        return engine, create_session_factory(engine)

    bootstrap_engine = create_engine(settings)
    try:
        await create_schema(bootstrap_engine)
    finally:
        await bootstrap_engine.dispose()
    return None, None


async def dispose_validation_resources(engine: AsyncEngine | None) -> None:
    """Dispose validation engine when initialized."""

    if engine is None:
        return
    await engine.dispose()
