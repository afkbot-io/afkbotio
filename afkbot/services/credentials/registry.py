"""Singleton registry for credentials service instances."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory
from afkbot.services.credentials.errors import CredentialsServiceError
from afkbot.services.credentials.vault import CredentialsVault, CredentialsVaultUnavailableError
from afkbot.settings import Settings

if TYPE_CHECKING:  # pragma: no cover
    from afkbot.services.credentials.service import CredentialsService

_SERVICES_BY_ROOT: dict[tuple[str, int], "CredentialsService"] = {}


def build_credentials_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    vault: CredentialsVault,
    engine: AsyncEngine | None = None,
) -> "CredentialsService":
    """Construct credentials service without affecting singleton registry."""

    from afkbot.services.credentials.service import CredentialsService

    return CredentialsService(session_factory, vault, engine=engine)


def get_credentials_service(settings: Settings) -> "CredentialsService":
    """Get or create credentials service scoped to the current async loop when available."""

    key_root = str(settings.root_dir.resolve())
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync CLI flows may call this before wrapping work with ``asyncio.run(...)``.
        # Returning a fresh service avoids leaking one async engine across multiple loops.
        return _build_root_service(settings)

    key = (key_root, id(loop))
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = _build_root_service(settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_credentials_services() -> None:
    """Reset cached credentials service instances for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_credentials_services_async() -> None:
    """Reset cached credentials services and dispose their owned async engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()


def _build_root_service(settings: Settings) -> "CredentialsService":
    """Construct one credentials service for the provided root settings."""

    try:
        vault = CredentialsVault(settings.credentials_master_keys)
    except CredentialsVaultUnavailableError as exc:
        raise CredentialsServiceError(error_code=exc.error_code, reason=exc.reason) from exc

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    return build_credentials_service(session_factory=factory, vault=vault, engine=engine)
