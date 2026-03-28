"""Repository helper functions for automation service flows."""

from __future__ import annotations

from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.errors import AutomationsServiceError


async def ensure_profile_exists(repo: AutomationRepository, profile_id: str) -> None:
    """Ensure target profile exists before mutating automation data."""

    if await repo.validate_profile_exists(profile_id):
        return
    raise AutomationsServiceError(error_code="profile_not_found", reason="Profile not found")
