"""Turn finalization and persistence helpers for AgentLoop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import json

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.repositories.pending_resume_envelope_repo import PendingResumeEnvelopeRepository
from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, ActionType, TurnResult
from afkbot.services.agent_loop.memory_runtime import MemoryRuntime
from afkbot.services.agent_loop.session_compaction import SessionCompactionService
from afkbot.services.agent_loop.session_retention import SessionRetentionService
from afkbot.services.agent_loop.state_machine import StateMachine

AsyncLogEvent = Callable[..., Awaitable[None]]
SanitizeValue = Callable[[object], object]


class TurnFinalizer:
    """Persist final turn artifacts for blocked, pending, completed, and cancelled flows."""

    def __init__(
        self,
        *,
        run_repo: RunRepository,
        pending_resume_repo: PendingResumeEnvelopeRepository,
        pending_secure_repo: PendingSecureRequestRepository,
        memory_runtime: MemoryRuntime,
        session_compaction: SessionCompactionService,
        session_retention: SessionRetentionService,
        log_event: AsyncLogEvent,
        sanitize_value: SanitizeValue,
        secure_request_ttl_sec: int,
    ) -> None:
        self._run_repo = run_repo
        self._pending_resume_repo = pending_resume_repo
        self._pending_secure_repo = pending_secure_repo
        self._memory_runtime = memory_runtime
        self._session_compaction = session_compaction
        self._session_retention = session_retention
        self._log_event = log_event
        self._sanitize_value = sanitize_value
        self._secure_request_ttl_sec = max(60, int(secure_request_ttl_sec))

    async def finalize_blocked_user_input(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        user_message: str,
        blocked_message: str,
        blocked_reason: str | None,
        machine_state: str,
        persist_turn: bool = True,
    ) -> TurnResult:
        """Persist deterministic block/finalize artifacts for rejected user input."""

        await self._run_repo.update_status(run_id, "completed")
        if persist_turn:
            await self._run_repo.create_chat_turn(
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=blocked_message,
            )
            await self._refresh_session_compaction(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
            )
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.block",
            payload={
                "user_message": user_message,
                "blocked_reason": blocked_reason,
                "state": machine_state,
            },
        )
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.finalize",
            payload={
                "user_message": user_message,
                "assistant_message": blocked_message,
                "blocked_reason": blocked_reason,
                "state": machine_state,
            },
        )
        return TurnResult(
            run_id=run_id,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(
                action="block",
                message=blocked_message,
                blocked_reason=blocked_reason,
            ),
        )

    async def finalize_pending_envelope(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        user_message: str,
        machine_state: str,
        envelope: ActionEnvelope,
        persist_turn: bool = True,
    ) -> TurnResult:
        """Persist pending secure/profile/approval envelope and corresponding turn logs."""

        if envelope.action == "request_secure_field":
            if persist_turn:
                await self._persist_pending_secure_request(
                    run_id=run_id,
                    session_id=session_id,
                    profile_id=profile_id,
                    envelope=envelope,
                )
            event_type = "turn.request_secure_field"
        elif envelope.action == "ask_question":
            event_type = "turn.ask_question"
        else:
            event_type = "turn.pending"
        if persist_turn:
            await self._persist_pending_resume_envelope(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=envelope,
            )

        await self._run_repo.update_status(run_id, "completed")
        if persist_turn:
            await self._run_repo.create_chat_turn(
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=envelope.message,
            )
            await self._refresh_session_compaction(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
            )
        payload = {
            "user_message": user_message,
            "assistant_message": envelope.message,
            "question_id": envelope.question_id,
            "spec_patch": self._sanitize_value(envelope.spec_patch),
            "state": machine_state,
        }
        if envelope.action == "request_secure_field":
            payload["secure_field"] = envelope.secure_field
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        return TurnResult(
            run_id=run_id,
            session_id=session_id,
            profile_id=profile_id,
            envelope=envelope,
        )

    async def finalize_turn(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
        action: ActionType,
        blocked_reason: str | None,
        machine_state: str,
        policy: ProfilePolicy,
        runtime_metadata: dict[str, object] | None = None,
        spec_patch: dict[str, object] | None = None,
        persist_turn: bool = True,
    ) -> TurnResult:
        """Persist finalized turn artifacts, including optional auto-memory save."""

        if persist_turn:
            await self._memory_runtime.auto_save_turn(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=assistant_message,
                action=action,
                policy=policy,
                runtime_metadata=runtime_metadata,
            )
        await self._run_repo.update_status(run_id, "completed")
        if persist_turn:
            await self._run_repo.create_chat_turn(
                session_id=session_id,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            await self._refresh_session_compaction(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
            )
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.finalize",
            payload={
                "user_message": user_message,
                "assistant_message": assistant_message,
                "blocked_reason": blocked_reason,
                "spec_patch": self._sanitize_value(spec_patch),
                "state": machine_state,
            },
        )
        return TurnResult(
            run_id=run_id,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(
                action=action,
                message=assistant_message,
                blocked_reason=blocked_reason,
                spec_patch=spec_patch,
            ),
        )

    async def finalize_cancelled_turn(
        self,
        *,
        run_id: int,
        session_id: str,
        machine: StateMachine,
    ) -> None:
        """Persist cancellation status and cancellation event for one run."""

        try:
            machine.cancel()
        except ValueError:
            pass
        await self._run_repo.update_status(run_id, "cancelled")
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.cancel",
            payload={"state": machine.state.value},
        )

    async def _persist_pending_secure_request(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        envelope: ActionEnvelope,
    ) -> None:
        """Persist pending secure request for anti-replay checks on secure submit."""

        patch = envelope.spec_patch or {}
        question_id = (envelope.question_id or "").strip()
        secure_field = (envelope.secure_field or "").strip()
        integration_name = str(patch.get("integration_name") or "").strip()
        credential_name = str(patch.get("credential_name") or secure_field).strip()
        credential_profile_key = str(patch.get("credential_profile_key") or "default").strip()
        tool_name = str(patch.get("tool_name") or "").strip() or None
        nonce = str(patch.get("secure_nonce") or "").strip()
        if not question_id or not secure_field or not integration_name or not credential_name or not nonce:
            return
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._secure_request_ttl_sec)
        await self._pending_secure_repo.create(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            question_id=question_id,
            secure_field=secure_field,
            integration_name=integration_name,
            credential_name=credential_name,
            credential_profile_key=credential_profile_key,
            tool_name=tool_name,
            nonce=nonce,
            expires_at=expires_at,
        )

    async def _persist_pending_resume_envelope(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        envelope: ActionEnvelope,
    ) -> None:
        """Persist trusted replay payload separately from sanitized runlog storage."""

        action = envelope.action.strip()
        question_id = (envelope.question_id or "").strip()
        if action not in {"ask_question", "request_secure_field"} or not question_id:
            return
        spec_patch_json: str | None = None
        if envelope.spec_patch is not None:
            spec_patch_json = json.dumps(
                envelope.spec_patch,
                ensure_ascii=True,
                sort_keys=True,
                default=str,
            )
        await self._pending_resume_repo.create(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            question_id=question_id,
            action=action,
            secure_field=envelope.secure_field,
            spec_patch_json=spec_patch_json,
        )

    async def _refresh_session_compaction(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
    ) -> None:
        """Refresh trusted session summary after one persisted turn when needed."""

        result = await self._session_compaction.refresh_if_needed(
            profile_id=profile_id,
            session_id=session_id,
        )
        if not result.updated:
            gc_result = await self._session_retention.garbage_collect_session(
                profile_id=profile_id,
                session_id=session_id,
            )
            if gc_result.deleted_turn_count < 1:
                return
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="session.compaction.gc",
                payload={
                    "deleted_turn_count": gc_result.deleted_turn_count,
                    "scanned_session_count": gc_result.scanned_session_count,
                },
            )
            return
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="session.compaction.refresh",
            payload={
                "compacted_until_turn_id": result.compacted_until_turn_id,
                "source_turn_count": result.source_turn_count,
                "new_turn_count": result.new_turn_count,
            },
        )
        gc_result = await self._session_retention.garbage_collect_session(
            profile_id=profile_id,
            session_id=session_id,
        )
        if gc_result.deleted_turn_count < 1:
            return
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="session.compaction.gc",
            payload={
                "deleted_turn_count": gc_result.deleted_turn_count,
                "scanned_session_count": gc_result.scanned_session_count,
            },
        )
