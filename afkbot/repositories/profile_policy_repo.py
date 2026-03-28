"""Repository for profile policy entities."""

from __future__ import annotations

import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.profile_policy import ProfilePolicy


class ProfilePolicyRepository:
    """Persistence operations for ProfilePolicy model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: str) -> ProfilePolicy | None:
        """Get profile policy by profile id."""

        return await self._session.get(ProfilePolicy, profile_id)

    async def get_or_create_default(self, profile_id: str) -> ProfilePolicy:
        """Ensure default policy exists for profile."""

        existing = await self.get(profile_id)
        if existing is not None:
            return existing
        policy = ProfilePolicy(profile_id=profile_id)
        self._session.add(policy)
        try:
            await self._session.flush()
            return policy
        except IntegrityError:
            await self._session.rollback()
            row = await self.get(profile_id)
            if row is None:
                raise
            return row

    async def apply_resolved_policy(
        self,
        *,
        profile_id: str,
        policy_enabled: bool,
        policy_preset: str,
        policy_capabilities: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        allowed_directories: tuple[str, ...],
        max_iterations_main: int,
        max_iterations_subagent: int,
        network_allowlist: tuple[str, ...],
    ) -> ProfilePolicy:
        """Apply one resolved policy snapshot to a profile row."""

        row = await self.get_or_create_default(profile_id)
        row.policy_enabled = bool(policy_enabled)
        row.policy_preset = policy_preset.strip() or "medium"
        row.policy_capabilities_json = json.dumps(
            list(policy_capabilities),
            ensure_ascii=True,
            sort_keys=True,
        )
        row.max_iterations_main = max(1, int(max_iterations_main))
        row.max_iterations_subagent = max(0, int(max_iterations_subagent))
        row.allowed_tools_json = json.dumps(
            list(allowed_tools),
            ensure_ascii=True,
            sort_keys=True,
        )
        normalized_allowed_directories = sorted(
            {
                item.strip()
                for item in allowed_directories
                if item and item.strip()
            }
        )
        row.allowed_directories_json = json.dumps(
            normalized_allowed_directories,
            ensure_ascii=True,
            sort_keys=True,
        )
        row.denied_tools_json = "[]"
        normalized_network_allowlist = sorted(
            {
                item.strip().lower()
                for item in network_allowlist
                if item and item.strip()
            }
        )
        row.network_allowlist_json = json.dumps(
            normalized_network_allowlist,
            ensure_ascii=True,
            sort_keys=True,
        )
        await self._session.flush()
        return row
