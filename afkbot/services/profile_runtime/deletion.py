"""Ordered cleanup helpers for deleting one profile-agent and all linked data."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.automation import Automation
from afkbot.models.automation_trigger_cron import AutomationTriggerCron
from afkbot.models.automation_trigger_webhook import AutomationTriggerWebhook
from afkbot.models.automation_webhook_processed_event import AutomationWebhookProcessedEvent
from afkbot.models.channel_binding import ChannelBinding
from afkbot.models.channel_endpoint import ChannelEndpoint
from afkbot.models.chat_session import ChatSession
from afkbot.models.chat_session_compaction import ChatSessionCompaction
from afkbot.models.chat_session_turn_queue import ChatSessionTurnQueueItem
from afkbot.models.chat_turn import ChatTurn
from afkbot.models.chat_turn_idempotency import ChatTurnIdempotency, ChatTurnIdempotencyClaim
from afkbot.models.connect_access_token import ConnectAccessToken
from afkbot.models.connect_claim_token import ConnectClaimToken
from afkbot.models.connect_session_token import ConnectSessionToken
from afkbot.models.credential_profile import CredentialProfile
from afkbot.models.memory_item import MemoryItem
from afkbot.models.pending_secure_request import PendingSecureRequest
from afkbot.models.profile import Profile
from afkbot.models.profile_policy import ProfilePolicy
from afkbot.models.run import Run
from afkbot.models.runlog_event import RunlogEvent
from afkbot.models.secret import Secret
from afkbot.models.subagent_task import SubagentTask
from afkbot.models.tool_credential_binding import ToolCredentialBinding
from afkbot.services.channels.endpoint_service import ChannelEndpointService


async def purge_profile_rows(
    *,
    session: AsyncSession,
    profile_id: str,
) -> tuple[str, ...]:
    """Delete one profile and all linked DB rows. Return removed channel endpoint ids."""

    automation_ids = tuple(
        int(item)
        for item in (
            await session.execute(select(Automation.id).where(Automation.profile_id == profile_id))
        ).scalars()
    )
    run_ids = tuple(
        int(item)
        for item in (
            await session.execute(select(Run.id).where(Run.profile_id == profile_id))
        ).scalars()
    )
    secret_ids = tuple(
        int(item)
        for item in (
            await session.execute(
                select(ToolCredentialBinding.secret_id).where(ToolCredentialBinding.profile_id == profile_id)
            )
        ).scalars()
        if item is not None
    )
    endpoint_ids = tuple(
        str(item)
        for item in (
            await session.execute(
                select(ChannelEndpoint.endpoint_id).where(ChannelEndpoint.profile_id == profile_id)
            )
        ).scalars()
    )

    if automation_ids:
        await session.execute(
            delete(AutomationWebhookProcessedEvent).where(
                AutomationWebhookProcessedEvent.automation_id.in_(automation_ids)
            )
        )
        await session.execute(
            delete(AutomationTriggerCron).where(AutomationTriggerCron.automation_id.in_(automation_ids))
        )
        await session.execute(
            delete(AutomationTriggerWebhook).where(
                AutomationTriggerWebhook.automation_id.in_(automation_ids)
            )
        )

    await session.execute(delete(PendingSecureRequest).where(PendingSecureRequest.profile_id == profile_id))
    await session.execute(
        delete(ChatSessionTurnQueueItem).where(ChatSessionTurnQueueItem.profile_id == profile_id)
    )
    await session.execute(
        delete(ChatTurnIdempotencyClaim).where(ChatTurnIdempotencyClaim.profile_id == profile_id)
    )
    await session.execute(delete(ChatTurnIdempotency).where(ChatTurnIdempotency.profile_id == profile_id))
    await session.execute(delete(ConnectAccessToken).where(ConnectAccessToken.profile_id == profile_id))

    if run_ids:
        await session.execute(delete(RunlogEvent).where(RunlogEvent.run_id.in_(run_ids)))

    await session.execute(delete(ChatSessionCompaction).where(ChatSessionCompaction.profile_id == profile_id))
    await session.execute(delete(ChatTurn).where(ChatTurn.profile_id == profile_id))
    await session.execute(delete(Run).where(Run.profile_id == profile_id))
    await session.execute(delete(ChatSession).where(ChatSession.profile_id == profile_id))

    await session.execute(delete(ToolCredentialBinding).where(ToolCredentialBinding.profile_id == profile_id))
    if secret_ids:
        await session.execute(
            delete(Secret).where(
                Secret.id.in_(secret_ids),
                ~exists(select(ToolCredentialBinding.id).where(ToolCredentialBinding.secret_id == Secret.id)),
            )
        )
    await session.execute(delete(CredentialProfile).where(CredentialProfile.profile_id == profile_id))

    await session.execute(delete(ConnectSessionToken).where(ConnectSessionToken.profile_id == profile_id))
    await session.execute(delete(ConnectClaimToken).where(ConnectClaimToken.profile_id == profile_id))
    await session.execute(delete(Automation).where(Automation.profile_id == profile_id))

    await session.execute(delete(ChannelBinding).where(ChannelBinding.profile_id == profile_id))
    await session.execute(delete(ChannelEndpoint).where(ChannelEndpoint.profile_id == profile_id))
    await session.execute(delete(MemoryItem).where(MemoryItem.profile_id == profile_id))
    await session.execute(delete(SubagentTask).where(SubagentTask.profile_id == profile_id))
    await session.execute(delete(ProfilePolicy).where(ProfilePolicy.profile_id == profile_id))
    await session.execute(delete(Profile).where(Profile.id == profile_id))
    await session.flush()
    return endpoint_ids


def remove_profile_files(*, profile_root: Path, endpoint_service: ChannelEndpointService, endpoint_ids: tuple[str, ...]) -> None:
    """Remove profile workspace tree and detached endpoint state after DB commit."""

    for endpoint_id in endpoint_ids:
        endpoint_service.remove_state(endpoint_id=endpoint_id)
    if profile_root.exists():
        shutil.rmtree(profile_root)
