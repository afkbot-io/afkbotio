"""Interactive-resume helpers for API-facing AgentLoop adapters."""

from __future__ import annotations

from typing import Protocol

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, ActionType, TurnResult
from afkbot.services.agent_loop.interactive_resume import (
    apply_approval_to_resume_call,
    apply_profile_name_to_resume_call,
    available_profile_choices,
    build_secure_resume_message,
    extract_resume_tool_call,
    is_credential_profile_question,
)
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.tools.base import ToolCall


class PendingEnvelopeResolver(Protocol):
    """Resolve one trusted pending action envelope from server-side storage."""

    async def __call__(
        self,
        *,
        profile_id: str,
        session_id: str,
        question_id: str | None,
        action: ActionType,
        secure_field: str | None = None,
    ) -> ActionEnvelope | None: ...


class ChatTurnRunner(Protocol):
    """Run one chat turn through the canonical API runtime entrypoint."""

    async def __call__(
        self,
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None = None,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult: ...


def _blocked_turn_result(
    *,
    session_id: str,
    profile_id: str,
    message: str,
    blocked_reason: str,
) -> TurnResult:
    """Build a deterministic blocked turn result for interactive resume failures."""

    return TurnResult(
        run_id=0,
        session_id=session_id,
        profile_id=profile_id,
        envelope=ActionEnvelope(
            action="block",
            message=message,
            blocked_reason=blocked_reason,
        ),
    )


def _finalized_turn_result(
    *,
    session_id: str,
    profile_id: str,
    message: str,
) -> TurnResult:
    """Build a deterministic finalize result without replaying runtime work."""

    return TurnResult(
        run_id=0,
        session_id=session_id,
        profile_id=profile_id,
        envelope=ActionEnvelope(
            action="finalize",
            message=message,
        ),
    )


async def _resume_profile_selection(
    *,
    envelope: ActionEnvelope,
    profile_id: str,
    session_id: str,
    answer_text: str | None,
    client_msg_id: str | None,
    context_overrides: TurnContextOverrides | None,
    run_chat_turn_call: ChatTurnRunner,
) -> TurnResult:
    """Resume a pending credential-profile selection with one chosen profile."""

    available_profiles = available_profile_choices(envelope)
    if not available_profiles:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message=(
                "Credential profile selection failed: available credential profiles are missing."
            ),
            blocked_reason="credential_profile_choices_missing",
        )
    selected_profile = str(answer_text or "").strip()
    if selected_profile not in available_profiles:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message=(
                "Credential profile selection failed: choose one of "
                f"{', '.join(available_profiles)}."
            ),
            blocked_reason="credential_profile_invalid_choice",
        )
    resume_call = extract_resume_tool_call(envelope)
    if resume_call is None:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message="Credential profile selection failed: resume tool payload is missing.",
            blocked_reason="credential_profile_resume_payload_missing",
        )
    resumed_call = apply_profile_name_to_resume_call(
        tool_call=resume_call,
        profile_name=selected_profile,
    )
    return await run_chat_turn_call(
        message=f"profile_resume:{resumed_call.name}",
        profile_id=profile_id,
        session_id=session_id,
        client_msg_id=client_msg_id or f"answer:{envelope.question_id or 'profile'}:{selected_profile}",
        planned_tool_calls=[resumed_call],
        context_overrides=context_overrides,
    )


async def resume_chat_interaction_flow(
    *,
    envelope: ActionEnvelope,
    profile_id: str,
    session_id: str,
    approved: bool | None,
    answer_text: str | None,
    client_msg_id: str | None,
    context_overrides: TurnContextOverrides | None,
    resolve_pending_envelope: PendingEnvelopeResolver,
    run_chat_turn_call: ChatTurnRunner,
) -> TurnResult:
    """Resume one ask-question flow using trusted pending-envelope state."""

    trusted_envelope = await resolve_pending_envelope(
        profile_id=profile_id,
        session_id=session_id,
        question_id=envelope.question_id,
        action="ask_question",
    )
    if trusted_envelope is None:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message="Interactive answer failed: question is invalid or expired.",
            blocked_reason="interactive_question_invalid",
        )
    envelope = trusted_envelope

    if is_credential_profile_question(envelope):
        return await _resume_profile_selection(
            envelope=envelope,
            profile_id=profile_id,
            session_id=session_id,
            answer_text=answer_text,
            client_msg_id=client_msg_id,
            context_overrides=context_overrides,
            run_chat_turn_call=run_chat_turn_call,
        )

    if approved is False:
        return _finalized_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message="Operation cancelled: confirmation denied by user.",
        )

    normalized_answer_text = str(answer_text or "").strip()
    if normalized_answer_text and approved is not True:
        return await run_chat_turn_call(
            message=normalized_answer_text,
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id or f"answer:{envelope.question_id or 'question'}",
            planned_tool_calls=None,
            context_overrides=context_overrides,
        )

    if approved is not True:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message="Safety confirmation failed: approval answer is missing.",
            blocked_reason="approval_answer_missing",
        )

    resume_call = extract_resume_tool_call(envelope)
    if resume_call is None:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message="Safety confirmation failed: resume tool payload is missing.",
            blocked_reason="approval_resume_payload_missing",
        )
    resumed_call = apply_approval_to_resume_call(
        tool_call=resume_call,
        question_id=envelope.question_id,
    )
    return await run_chat_turn_call(
        message=f"approval_resume:{resumed_call.name}",
        profile_id=profile_id,
        session_id=session_id,
        client_msg_id=client_msg_id or f"answer:{envelope.question_id or 'approval'}:approved",
        planned_tool_calls=[resumed_call],
        context_overrides=context_overrides,
    )


async def resume_chat_after_secure_submit_flow(
    *,
    envelope: ActionEnvelope,
    profile_id: str,
    session_id: str,
    client_msg_id: str | None,
    context_overrides: TurnContextOverrides | None,
    resolve_pending_envelope: PendingEnvelopeResolver,
    run_chat_turn_call: ChatTurnRunner,
) -> TurnResult:
    """Resume one secure-field flow after server-side secret capture completes."""

    trusted_envelope = await resolve_pending_envelope(
        profile_id=profile_id,
        session_id=session_id,
        question_id=envelope.question_id,
        action="request_secure_field",
        secure_field=envelope.secure_field,
    )
    if trusted_envelope is None:
        return _blocked_turn_result(
            session_id=session_id,
            profile_id=profile_id,
            message="Secure resume failed: request is invalid or expired.",
            blocked_reason="secure_resume_request_invalid",
        )
    envelope = trusted_envelope

    resume_call = extract_resume_tool_call(envelope)
    if resume_call is None:
        return await run_chat_turn_call(
            message=build_secure_resume_message(envelope),
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id or f"secure:{envelope.question_id or 'resume'}",
            planned_tool_calls=None,
            context_overrides=context_overrides,
        )

    return await run_chat_turn_call(
        message=f"secure_resume:{resume_call.name}",
        profile_id=profile_id,
        session_id=session_id,
        client_msg_id=client_msg_id or f"secure:{envelope.question_id or resume_call.name}",
        planned_tool_calls=[resume_call],
        context_overrides=context_overrides,
    )
