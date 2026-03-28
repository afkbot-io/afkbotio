"""Repository for automation entities and trigger rows."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.automation_repo_claims import AutomationRepositoryClaimsMixin
from afkbot.repositories.automation_repo_crud import AutomationRepositoryCrudMixin


class AutomationRepository(AutomationRepositoryClaimsMixin, AutomationRepositoryCrudMixin):
    """Persistence operations for profile automation entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
