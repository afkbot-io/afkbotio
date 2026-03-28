"""Service for persisted external channel endpoint configs."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.channel_endpoint_repo import ChannelEndpointRepository
from afkbot.repositories.channel_ingress_event_repo import ChannelIngressEventRepository
from afkbot.repositories.channel_ingress_pending_event_repo import ChannelIngressPendingEventRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    deserialize_endpoint_config,
    validate_channel_endpoint_id,
)
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[tuple[str, int], "ChannelEndpointService"] = {}
TEndpointValue = TypeVar("TEndpointValue")


class ChannelEndpointServiceError(ValueError):
    """Structured endpoint service error."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class ChannelEndpointService:
    """Manage persisted external channel adapter endpoints."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def create(self, config: ChannelEndpointConfig) -> ChannelEndpointConfig:
        """Create one endpoint config and fail if it already exists."""

        async def _op(session: AsyncSession) -> ChannelEndpointConfig:
            await self._ensure_profile_exists(session=session, profile_id=config.profile_id)
            existing = await ChannelEndpointRepository(session).get(config.endpoint_id)
            if existing is not None:
                raise ChannelEndpointServiceError(
                    error_code="channel_endpoint_exists",
                    reason=f"Channel endpoint already exists: {config.endpoint_id}",
                )
            row = await ChannelEndpointRepository(session).put(config)
            return self._to_config(row)

        return await self._with_session(_op)

    async def update(self, config: ChannelEndpointConfig) -> ChannelEndpointConfig:
        """Update one endpoint config and fail if it does not exist."""

        async def _op(session: AsyncSession) -> ChannelEndpointConfig:
            await self._ensure_profile_exists(session=session, profile_id=config.profile_id)
            existing = await ChannelEndpointRepository(session).get(config.endpoint_id)
            if existing is None:
                raise ChannelEndpointServiceError(
                    error_code="channel_endpoint_not_found",
                    reason=f"Channel endpoint not found: {config.endpoint_id}",
                )
            row = await ChannelEndpointRepository(session).put(config)
            return self._to_config(row)

        return await self._with_session(_op)

    async def get(self, *, endpoint_id: str) -> ChannelEndpointConfig:
        """Return one endpoint config by id."""

        async def _op(session: AsyncSession) -> ChannelEndpointConfig:
            row = await ChannelEndpointRepository(session).get(validate_channel_endpoint_id(endpoint_id))
            if row is None:
                raise ChannelEndpointServiceError(
                    error_code="channel_endpoint_not_found",
                    reason=f"Channel endpoint not found: {endpoint_id}",
                )
            return self._to_config(row)

        return await self._with_session(_op)

    async def list(
        self,
        *,
        transport: str | None = None,
        enabled: bool | None = None,
        profile_id: str | None = None,
        endpoint_ids: tuple[str, ...] | None = None,
    ) -> list[ChannelEndpointConfig]:
        """List endpoint configs filtered by optional selectors."""

        async def _op(session: AsyncSession) -> list[ChannelEndpointConfig]:
            rows = await ChannelEndpointRepository(session).list_all(
                transport=transport,
                enabled=enabled,
                profile_id=profile_id,
                endpoint_ids=endpoint_ids,
            )
            return [self._to_config(row) for row in rows]

        return await self._with_session(_op)

    async def delete(self, *, endpoint_id: str) -> bool:
        """Delete one endpoint config and its persisted adapter state."""

        normalized_id = validate_channel_endpoint_id(endpoint_id)

        async def _op(session: AsyncSession) -> bool:
            await ChannelIngressEventRepository(session).delete_by_endpoint(endpoint_id=normalized_id)
            await ChannelIngressPendingEventRepository(session).delete_by_endpoint(endpoint_id=normalized_id)
            deleted = await ChannelEndpointRepository(session).delete(normalized_id)
            if not deleted:
                raise ChannelEndpointServiceError(
                    error_code="channel_endpoint_not_found",
                    reason=f"Channel endpoint not found: {endpoint_id}",
                )
            return True

        deleted = await self._with_session(_op)
        self.remove_state(endpoint_id=normalized_id)
        return deleted

    async def delete_by_profile(self, *, profile_id: str) -> tuple[str, ...]:
        """Delete all channel endpoints linked to one profile and remove their state dirs."""

        endpoints = await self.list(profile_id=profile_id)
        if not endpoints:
            return ()

        endpoint_ids = tuple(item.endpoint_id for item in endpoints)

        async def _op(session: AsyncSession) -> tuple[str, ...]:
            repo = ChannelEndpointRepository(session)
            ingress_repo = ChannelIngressEventRepository(session)
            pending_ingress_repo = ChannelIngressPendingEventRepository(session)
            for endpoint_id in endpoint_ids:
                await ingress_repo.delete_by_endpoint(endpoint_id=endpoint_id)
                await pending_ingress_repo.delete_by_endpoint(endpoint_id=endpoint_id)
                await repo.delete(endpoint_id)
            return endpoint_ids

        removed = await self._with_session(_op)
        for endpoint_id in removed:
            self.remove_state(endpoint_id=endpoint_id)
        return removed

    def state_dir(self, *, endpoint_id: str) -> Path:
        """Return absolute state directory for one endpoint."""

        normalized = validate_channel_endpoint_id(endpoint_id)
        return self._settings.profiles_dir / ".system" / "channels" / normalized

    def telegram_polling_state_path(self, *, endpoint_id: str) -> Path:
        """Return persisted Telegram polling state file for one endpoint."""

        return self.state_dir(endpoint_id=endpoint_id) / "telegram_polling_state.json"

    def telethon_user_state_path(self, *, endpoint_id: str) -> Path:
        """Return persisted Telethon userbot state file for one endpoint."""

        return self.state_dir(endpoint_id=endpoint_id) / "telethon_user_state.json"

    def state_file(self, *, endpoint_id: str, name: str) -> Path:
        """Return one arbitrary state file inside endpoint-owned state dir."""

        normalized = (name or "").strip()
        if not normalized:
            raise ValueError("state file name is required")
        return self.state_dir(endpoint_id=endpoint_id) / normalized

    def remove_state(self, *, endpoint_id: str) -> None:
        """Remove persisted adapter state for one endpoint when present."""

        state_dir = self.state_dir(endpoint_id=endpoint_id)
        if state_dir.exists():
            shutil.rmtree(state_dir)

    @staticmethod
    def _to_config(row: object) -> ChannelEndpointConfig:
        raw_config = getattr(row, "config_json", "{}")
        try:
            parsed_config = json.loads(raw_config) if isinstance(raw_config, str) else {}
        except json.JSONDecodeError:
            parsed_config = {}
        if not isinstance(parsed_config, dict):
            parsed_config = {}
        return deserialize_endpoint_config(
            {
                "endpoint_id": getattr(row, "endpoint_id"),
                "transport": getattr(row, "transport"),
                "adapter_kind": getattr(row, "adapter_kind"),
                "profile_id": getattr(row, "profile_id"),
                "credential_profile_key": getattr(row, "credential_profile_key"),
                "account_id": getattr(row, "account_id"),
                "enabled": getattr(row, "enabled"),
                "group_trigger_mode": getattr(row, "group_trigger_mode", None),
                "config": parsed_config,
            }
        )

    async def _ensure_profile_exists(self, *, session: AsyncSession, profile_id: str) -> None:
        row = await ProfileRepository(session).get(profile_id)
        if row is not None:
            return
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_profile_not_found",
            reason=f"Profile not found: {profile_id}",
        )

    async def _with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TEndpointValue]],
    ) -> TEndpointValue:
        await self._ensure_schema()
        async with session_scope(self._session_factory) as session:
            return await op(session)

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await create_schema(self._engine)
            self._schema_ready = True

    async def shutdown(self) -> None:
        """Dispose owned database engine."""

        await self._engine.dispose()


def get_channel_endpoint_service(settings: Settings) -> ChannelEndpointService:
    """Return a channel endpoint service scoped to the current async loop when available."""

    key_root = str(settings.root_dir.resolve())
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync CLI flows often call this before wrapping work with ``asyncio.run(...)``.
        # Returning a fresh service avoids leaking one async engine across multiple loops.
        return ChannelEndpointService(settings=settings)

    key = (key_root, id(loop))
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ChannelEndpointService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def run_channel_endpoint_service_sync(
    settings: Settings,
    op: Callable[[ChannelEndpointService], Awaitable[TEndpointValue]],
) -> TEndpointValue:
    """Run one endpoint-service operation in a fresh event loop and dispose the engine."""

    async def _run() -> TEndpointValue:
        service = ChannelEndpointService(settings=settings)
        try:
            return await op(service)
        finally:
            await service.shutdown()

    return asyncio.run(_run())


def channel_endpoint_state_dir(settings: Settings, *, endpoint_id: str) -> Path:
    """Return endpoint-owned state directory without constructing a DB-backed service."""

    normalized = validate_channel_endpoint_id(endpoint_id)
    return settings.profiles_dir / ".system" / "channels" / normalized


def telegram_polling_state_path_for(settings: Settings, *, endpoint_id: str) -> Path:
    """Return Telegram polling state path without constructing a DB-backed service."""

    return channel_endpoint_state_dir(settings, endpoint_id=endpoint_id) / "telegram_polling_state.json"


def telethon_user_state_path_for(settings: Settings, *, endpoint_id: str) -> Path:
    """Return Telethon user state path without constructing a DB-backed service."""

    return channel_endpoint_state_dir(settings, endpoint_id=endpoint_id) / "telethon_user_state.json"


def reset_channel_endpoint_services() -> None:
    """Reset cached endpoint services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_channel_endpoint_services_async() -> None:
    """Reset cached endpoint services and dispose their DB engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
