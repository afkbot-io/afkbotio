"""Runtime helpers for one-turn execution, secure input replay, and progress streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.session_orchestration import SessionOrchestrator, SessionTurnSource
from afkbot.services.session_orchestration.contracts import SerializedSessionTurnRunner
from afkbot.services.tools.base import ToolCall
from afkbot.settings import Settings, get_settings

ProgressSink = Callable[[ProgressEvent], None]


async def run_once_result(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    planned_tool_calls: list[ToolCall] | None = None,
    progress_sink: ProgressSink | None = None,
    context_overrides: TurnContextOverrides | None = None,
    source: SessionTurnSource = "chat",
) -> TurnResult:
    """Run one loop turn and return typed result payload."""

    effective_settings = settings or get_settings()
    return await SessionOrchestrator(
        settings=effective_settings,
        session_factory=session_factory,
    ).run_turn(
        message=message,
        profile_id=profile_id,
        session_id=session_id,
        planned_tool_calls=planned_tool_calls,
        progress_sink=progress_sink,
        context_overrides=context_overrides,
        source=source,
    )


@asynccontextmanager
async def open_serialized_turn_runner(
    *,
    profile_id: str,
    session_id: str,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    source: SessionTurnSource = "chat",
) -> AsyncIterator[SerializedSessionTurnRunner]:
    """Open one exclusive session slot that can execute several turns in sequence."""

    effective_settings = settings or get_settings()
    orchestrator = SessionOrchestrator(
        settings=effective_settings,
        session_factory=session_factory,
    )
    async with orchestrator.open_turn_lease(
        profile_id=profile_id,
        session_id=session_id,
        source=source,
    ) as lease:
        yield lease


async def submit_secure_field(
    *,
    profile_id: str,
    envelope: ActionEnvelope,
    secret_value: str,
    session_id: str | None = None,
) -> tuple[bool, str]:
    """Persist one secure credential value requested by `request_secure_field` envelope."""

    patch = envelope.spec_patch or {}
    question_id = (envelope.question_id or "").strip()
    secure_field = (envelope.secure_field or "").strip()
    secure_nonce = str(patch.get("secure_nonce") or "").strip()
    resolved_session_id = (session_id or "").strip()
    if not resolved_session_id:
        return False, "session_id_missing"
    if not question_id:
        return False, "question_id_missing"
    if not secure_field:
        return False, "credential_name_missing"
    if not secure_nonce:
        return False, "secure_nonce_missing"

    if secret_value == "":
        return False, "secret_value_empty"

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    pending_id: int | None = None
    try:
        async with session_scope(session_factory) as db:
            pending_repo = PendingSecureRequestRepository(db)
            pending = await pending_repo.claim_active(
                profile_id=profile_id,
                session_id=resolved_session_id,
                question_id=question_id,
                secure_field=secure_field,
                nonce=secure_nonce,
            )
            if pending is None:
                return False, "secure_request_invalid_or_expired"
            pending_id = pending.id
            integration_name = pending.integration_name
            credential_name = pending.credential_name
            credential_profile_key = pending.credential_profile_key
            tool_name = pending.tool_name or None
            if tool_name == "credentials.request":
                tool_name = "app.run"

        service = get_credentials_service(settings)
        await service.create(
            profile_id=profile_id,
            tool_name=tool_name,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
            replace_existing=True,
        )
        return True, "ok"
    except CredentialsServiceError as exc:
        if pending_id is not None:
            async with session_scope(session_factory) as db:
                await PendingSecureRequestRepository(db).release_claim(row_id=pending_id)
        return False, exc.error_code
    except Exception:
        if pending_id is not None:
            async with session_scope(session_factory) as db:
                await PendingSecureRequestRepository(db).release_claim(row_id=pending_id)
        raise
    finally:
        await engine.dispose()
