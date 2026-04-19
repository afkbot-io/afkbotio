"""Shared harness for credentials tool integration tests."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.credentials import reset_credentials_services_async
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


async def prepare_credentials_tools(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    extra_plugins: tuple[str, ...] = (),
) -> tuple[Settings, AsyncEngine, async_sessionmaker[AsyncSession], ToolRegistry]:
    """Build one isolated tools registry with credentials enabled."""

    key = Fernet.generate_key().decode("utf-8")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tools_credentials.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", key)
    get_settings.cache_clear()

    settings = get_settings()
    await reset_credentials_services_async()

    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    plugin_names = tuple(dict.fromkeys((*settings.enabled_tool_plugins, *extra_plugins)))
    return settings, engine, factory, ToolRegistry.from_plugins(plugin_names, settings=settings)


async def create_credential(
    *,
    registry: ToolRegistry,
    settings: Settings,
    ctx: ToolContext,
    app_name: str,
    profile_name: str,
    credential_slug: str,
    value: str,
) -> None:
    """Create one credential through the public plugin surface."""

    create_tool = registry.get("credentials.create")
    assert create_tool is not None
    params = create_tool.parse_params(
        {
            "profile_key": ctx.profile_id,
            "app_name": app_name,
            "profile_name": profile_name,
            "credential_slug": credential_slug,
            "value": value,
            "replace_existing": True,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await create_tool.execute(ctx, params)
    assert result.ok is True
