"""Credentials service orchestration over repository and encrypted vault."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.services.credentials.errors import CredentialsServiceError
from afkbot.services.credentials.binding_mixin import CredentialsBindingMixin
from afkbot.services.credentials.profile_mixin import CredentialsProfileMixin
from afkbot.services.credentials.runtime_mixin import CredentialsRuntimeMixin
from afkbot.services.credentials.vault import (
    CredentialsVault,
    CredentialsVaultError,
)
TValue = TypeVar("TValue")


class CredentialsService(CredentialsBindingMixin, CredentialsProfileMixin, CredentialsRuntimeMixin):
    """Service for encrypted credentials lifecycle and metadata-only retrieval."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        vault: CredentialsVault,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vault = vault
        self._engine = engine

    async def _with_repo(
        self,
        op: Callable[[CredentialsRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = CredentialsRepository(session)
            return await op(repo)

    def _encrypt(self, secret_value: str) -> tuple[str, str]:
        try:
            return self._vault.encrypt(secret_value)
        except CredentialsVaultError as exc:
            raise CredentialsServiceError(error_code=exc.error_code, reason=exc.reason) from exc

    async def shutdown(self) -> None:
        """Dispose owned async engine when this service created it."""

        if self._engine is None:
            return
        await self._engine.dispose()
