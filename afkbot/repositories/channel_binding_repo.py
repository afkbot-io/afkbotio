"""Repository for persisted channel binding rules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.channel_binding import ChannelBinding
from afkbot.services.channel_routing.contracts import ChannelBindingRule


class ChannelBindingRepository:
    """Persistence operations for channel binding rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, binding_id: str) -> ChannelBinding | None:
        """Return one binding row by id."""

        return await self._session.get(ChannelBinding, binding_id)

    async def list_all(
        self,
        *,
        transport: str | None = None,
        profile_id: str | None = None,
    ) -> list[ChannelBinding]:
        """List binding rows filtered by optional transport/profile."""

        stmt = select(ChannelBinding).order_by(
            ChannelBinding.transport.asc(),
            ChannelBinding.binding_id.asc(),
        )
        if transport is not None:
            stmt = stmt.where(ChannelBinding.transport == transport.strip().lower())
        if profile_id is not None:
            stmt = stmt.where(ChannelBinding.profile_id == profile_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def put(self, rule: ChannelBindingRule) -> ChannelBinding:
        """Create or replace one binding row."""

        row = await self.get(rule.binding_id)
        if row is None:
            row = ChannelBinding(binding_id=rule.binding_id)
            self._session.add(row)
        row.transport = rule.transport.strip().lower()
        row.profile_id = rule.profile_id
        row.session_policy = rule.session_policy
        row.priority = rule.priority
        row.enabled = rule.enabled
        row.account_id = rule.account_id
        row.peer_id = rule.peer_id
        row.thread_id = rule.thread_id
        row.user_id = rule.user_id
        row.prompt_overlay = rule.prompt_overlay
        await self._session.flush()
        return row

    async def delete(self, binding_id: str) -> bool:
        """Delete one binding row by id."""

        row = await self.get(binding_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
