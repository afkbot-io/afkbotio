"""Repository for persisted channel endpoint configs."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.channel_endpoint import ChannelEndpoint
from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    serialize_endpoint_storage_payload,
)


class ChannelEndpointRepository:
    """Persistence operations for channel endpoint rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, endpoint_id: str) -> ChannelEndpoint | None:
        """Return one endpoint row by id."""

        return await self._session.get(ChannelEndpoint, endpoint_id)

    async def list_all(
        self,
        *,
        transport: str | None = None,
        enabled: bool | None = None,
        profile_id: str | None = None,
        endpoint_ids: tuple[str, ...] | None = None,
    ) -> list[ChannelEndpoint]:
        """List endpoint rows filtered by optional transport/enabled/profile/id set."""

        stmt = select(ChannelEndpoint).order_by(
            ChannelEndpoint.transport.asc(),
            ChannelEndpoint.endpoint_id.asc(),
        )
        if transport is not None:
            stmt = stmt.where(ChannelEndpoint.transport == transport.strip().lower())
        if enabled is not None:
            stmt = stmt.where(ChannelEndpoint.enabled == bool(enabled))
        if profile_id is not None:
            stmt = stmt.where(ChannelEndpoint.profile_id == profile_id)
        if endpoint_ids:
            stmt = stmt.where(ChannelEndpoint.endpoint_id.in_(endpoint_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def put(self, config: ChannelEndpointConfig) -> ChannelEndpoint:
        """Create or replace one endpoint row."""

        row = await self.get(config.endpoint_id)
        if row is None:
            row = ChannelEndpoint(endpoint_id=config.endpoint_id)
            self._session.add(row)
        group_trigger_mode, config_payload = serialize_endpoint_storage_payload(config)
        row.transport = config.transport
        row.adapter_kind = config.adapter_kind
        row.profile_id = config.profile_id
        row.credential_profile_key = config.credential_profile_key
        row.account_id = config.account_id
        row.enabled = config.enabled
        row.group_trigger_mode = group_trigger_mode
        row.config_json = json.dumps(
            config_payload,
            ensure_ascii=True,
            sort_keys=True,
        )
        await self._session.flush()
        return row

    async def delete(self, endpoint_id: str) -> bool:
        """Delete one endpoint row by id."""

        row = await self.get(endpoint_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
