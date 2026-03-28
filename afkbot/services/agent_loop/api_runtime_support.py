"""Support helpers for API-facing AgentLoop runtime orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
import json
import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_turn_idempotency_repo import ChatTurnIdempotencyRepository
from afkbot.repositories.pending_resume_envelope_repo import PendingResumeEnvelopeRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, ActionType, TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressCursor, ProgressEvent
from afkbot.settings import Settings, get_settings


IDEMPOTENCY_WAIT_TIMEOUT_SEC = 60.0
IDEMPOTENCY_WAIT_POLL_SEC = 0.05
IDEMPOTENCY_HEARTBEAT_SEC = 15.0


@dataclass(slots=True, frozen=True)
class ApiSessionResources:
    """Resolved session factory plus optional owned engine for one API operation."""

    session_factory: async_sessionmaker[AsyncSession]
    owned_engine: AsyncEngine | None = None


class ProgressPollResponse(BaseModel):
    """Progress poll response payload used by REST and websocket adapters."""

    model_config = ConfigDict(extra="forbid")

    events: list[ProgressEvent]
    cursor: ProgressCursor


async def resolve_session_resources(
    *,
    shared_session_factory: async_sessionmaker[AsyncSession] | None,
    settings: Settings | None = None,
) -> ApiSessionResources:
    """Return shared DB resources or create temporary owned resources on demand."""

    if shared_session_factory is not None:
        return ApiSessionResources(session_factory=shared_session_factory)

    resolved_settings = settings or get_settings()
    owned_engine = create_engine(resolved_settings)
    await create_schema(owned_engine)
    return ApiSessionResources(
        session_factory=create_session_factory(owned_engine),
        owned_engine=owned_engine,
    )


async def dispose_owned_engine(resources: ApiSessionResources) -> None:
    """Dispose the owned engine when one was created for the current operation."""

    if resources.owned_engine is not None:
        await resources.owned_engine.dispose()


def idempotency_row_to_turn_result(row: object) -> TurnResult:
    """Convert one idempotency row into a deterministic TurnResult payload."""

    profile_id = str(getattr(row, "profile_id"))
    session_id = str(getattr(row, "session_id"))
    run_id = int(getattr(row, "run_id"))
    envelope_json = getattr(row, "envelope_json")
    if not isinstance(envelope_json, str):
        envelope_payload: dict[str, object] = {}
    else:
        try:
            raw_payload = json.loads(envelope_json)
        except json.JSONDecodeError:
            raw_payload = {}
        if isinstance(raw_payload, dict):
            envelope_payload = {str(key): value for key, value in raw_payload.items()}
        else:
            envelope_payload = {}
    envelope = ActionEnvelope.model_validate(envelope_payload)
    return TurnResult(
        run_id=run_id,
        session_id=session_id,
        profile_id=profile_id,
        envelope=envelope,
    )


async def resolve_trusted_pending_envelope(
    *,
    shared_session_factory: async_sessionmaker[AsyncSession] | None,
    profile_id: str,
    session_id: str,
    question_id: str | None,
    action: ActionType,
    secure_field: str | None = None,
) -> ActionEnvelope | None:
    """Load one trusted pending envelope by question id from server-side resume storage."""

    normalized_question_id = str(question_id or "").strip()
    if not normalized_question_id:
        return None

    resources = await resolve_session_resources(shared_session_factory=shared_session_factory)
    try:
        async with session_scope(resources.session_factory) as db:
            row = await PendingResumeEnvelopeRepository(db).get_latest(
                profile_id=profile_id,
                session_id=session_id,
                question_id=normalized_question_id,
                action=action,
                secure_field=secure_field,
            )
        if row is None:
            return None
        raw_patch = row.spec_patch_json
        trusted_patch: dict[str, object] | None = None
        if isinstance(raw_patch, str) and raw_patch.strip():
            try:
                decoded_patch = json.loads(raw_patch)
            except json.JSONDecodeError:
                decoded_patch = None
            if isinstance(decoded_patch, dict):
                trusted_patch = {str(key): value for key, value in decoded_patch.items()}
        trusted_secure_field = (row.secure_field or "").strip() or None
        return ActionEnvelope(
            action=action,
            message="interactive resume",
            question_id=normalized_question_id,
            secure_field=trusted_secure_field,
            spec_patch=trusted_patch,
        )
    finally:
        await dispose_owned_engine(resources)


async def wait_for_claimed_turn_result(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    claim_cutoff_factory: Callable[..., datetime] | None = None,
) -> TurnResult | None:
    """Wait for the in-flight owner to persist a deterministic turn result."""

    deadline = time.monotonic() + IDEMPOTENCY_WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        async with session_scope(session_factory) as db:
            repo = ChatTurnIdempotencyRepository(db)
            existing = await repo.get_by_key(
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
            )
            if existing is not None:
                return idempotency_row_to_turn_result(existing)
            released = await repo.release_stale_claims(
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
                older_than=(
                    claim_cutoff_factory(settings=settings)
                    if claim_cutoff_factory is not None
                    else idempotency_claim_cutoff(settings=settings)
                ),
            )
            if released > 0:
                return None
            if not await repo.has_claim(
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
            ):
                return None
        await asyncio.sleep(IDEMPOTENCY_WAIT_POLL_SEC)
    raise TimeoutError(f"Timed out waiting for idempotent turn result for key {client_msg_id!r}")


def idempotency_claim_cutoff(*, settings: Settings) -> datetime:
    """Return the current cutoff for stale turn-execution claims."""

    ttl_sec = max(
        300.0,
        IDEMPOTENCY_WAIT_TIMEOUT_SEC * 4.0,
        float(settings.tool_timeout_max_sec * 3),
    )
    return datetime.now(UTC) - timedelta(seconds=ttl_sec)


async def heartbeat_turn_claim(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    owner_token: str,
    heartbeat_sec: float = IDEMPOTENCY_HEARTBEAT_SEC,
) -> None:
    """Refresh one in-flight claim until turn execution completes."""

    async with session_scope(session_factory) as db:
        refreshed = await ChatTurnIdempotencyRepository(db).touch_claim(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            owner_token=owner_token,
            touched_at=datetime.now(UTC),
        )
    if not refreshed:
        return

    while True:
        await asyncio.sleep(heartbeat_sec)
        async with session_scope(session_factory) as db:
            refreshed = await ChatTurnIdempotencyRepository(db).touch_claim(
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
                owner_token=owner_token,
                touched_at=datetime.now(UTC),
            )
        if not refreshed:
            return
