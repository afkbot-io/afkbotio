"""Tests for HTTP-oriented interactive resume helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from afkbot.db.session import session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.pending_resume_envelope_repo import PendingResumeEnvelopeRepository
from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.api_runtime import (
    _resolve_trusted_pending_envelope,
    resume_chat_after_secure_submit,
    resume_chat_interaction,
)
from afkbot.services.agent_loop.loop_sanitizer import sanitize_value
from afkbot.services.agent_loop.pending_envelopes import PROFILE_SELECTION_QUESTION_KIND
from afkbot.services.agent_loop.safety_policy import CONFIRM_ACK_PARAM, CONFIRM_QID_PARAM
from afkbot.services.agent_loop.turn_finalizer import TurnFinalizer
from afkbot.services.tools.base import ToolCall
from tests.services.agent_loop._loop_harness import create_test_db


async def test_resume_chat_interaction_approval_replays_with_confirmation(monkeypatch) -> None:
    """Approval answer should resume the original tool call with confirmation markers."""

    captured: dict[str, object] = {}

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        planned_tool_calls = kwargs["planned_tool_calls"]
        assert isinstance(planned_tool_calls, list)
        assert isinstance(planned_tool_calls[0], ToolCall)
        return TurnResult(
            run_id=2,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    envelope = ActionEnvelope(
        action="ask_question",
        message="confirm",
        question_id="approval-1",
        spec_patch={
            "tool_name": "debug.echo",
            "tool_params": {"message": "ok"},
        },
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    result = await resume_chat_interaction(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
        approved=True,
    )

    assert result.envelope.action == "finalize"
    assert captured["message"] == "approval_resume:debug.echo"
    planned_tool_calls = captured["planned_tool_calls"]
    assert isinstance(planned_tool_calls, list)
    assert planned_tool_calls[0].params[CONFIRM_ACK_PARAM] is True
    assert planned_tool_calls[0].params[CONFIRM_QID_PARAM] == "approval-1"


async def test_resume_chat_interaction_profile_selection_applies_profile_name(monkeypatch) -> None:
    """Credential profile answer should inject selected profile into replay call."""

    captured: dict[str, object] = {}

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        return TurnResult(
            run_id=2,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    envelope = ActionEnvelope(
        action="ask_question",
        message="choose profile",
        question_id="profile-1",
        spec_patch={
            "question_kind": PROFILE_SELECTION_QUESTION_KIND,
            "tool_name": "app.run",
            "tool_params": {
                "app_name": "telegram",
                "action": "get_me",
                "params": {},
            },
            "available_profile_keys": ["work", "personal"],
        },
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    result = await resume_chat_interaction(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
        answer_text="work",
    )

    assert result.envelope.action == "finalize"
    assert captured["message"] == "profile_resume:app.run"
    planned_tool_calls = captured["planned_tool_calls"]
    assert isinstance(planned_tool_calls, list)
    assert planned_tool_calls[0].params["profile_name"] == "work"


async def test_resume_chat_interaction_denied_confirmation_returns_finalize(monkeypatch) -> None:
    """Denied approval should return deterministic finalize envelope without replay."""

    called = False

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        nonlocal called
        called = True
        raise AssertionError("run_chat_turn must not be called on deny")

    envelope = ActionEnvelope(
        action="ask_question",
        message="confirm",
        question_id="approval-1",
        spec_patch={
            "tool_name": "debug.echo",
            "tool_params": {"message": "ok"},
        },
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    result = await resume_chat_interaction(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
        approved=False,
    )

    assert called is False
    assert result.envelope.action == "finalize"
    assert "cancelled" in result.envelope.message.lower()


async def test_resume_chat_interaction_text_answer_replays_as_user_message(monkeypatch) -> None:
    """Non-approval text answers should continue the chat turn instead of blocking."""

    # Arrange
    captured: dict[str, object] = {}

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        return TurnResult(
            run_id=4,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    envelope = ActionEnvelope(
        action="ask_question",
        message="what environment?",
        question_id="text-1",
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    # Act
    result = await resume_chat_interaction(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
        answer_text="production",
    )

    # Assert
    assert result.envelope.action == "finalize"
    assert captured["message"] == "production"
    assert captured["planned_tool_calls"] is None


async def test_resume_chat_interaction_approval_prioritizes_resume_over_answer_text(monkeypatch) -> None:
    """Approved safety confirmations should replay the pending tool call even with answer text."""

    # Arrange
    captured: dict[str, object] = {}

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        planned_tool_calls = kwargs["planned_tool_calls"]
        assert isinstance(planned_tool_calls, list)
        assert isinstance(planned_tool_calls[0], ToolCall)
        return TurnResult(
            run_id=5,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    envelope = ActionEnvelope(
        action="ask_question",
        message="confirm",
        question_id="approval-2",
        spec_patch={
            "tool_name": "debug.echo",
            "tool_params": {"message": "ok"},
        },
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    # Act
    result = await resume_chat_interaction(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
        approved=True,
        answer_text="yes",
    )

    # Assert
    assert result.envelope.action == "finalize"
    assert captured["message"] == "approval_resume:debug.echo"
    planned_tool_calls = captured["planned_tool_calls"]
    assert isinstance(planned_tool_calls, list)
    assert planned_tool_calls[0].params[CONFIRM_ACK_PARAM] is True
    assert planned_tool_calls[0].params[CONFIRM_QID_PARAM] == "approval-2"


async def test_resume_chat_after_secure_submit_replays_pending_tool(monkeypatch) -> None:
    """Secure submit resume should continue from pending tool call without raw rerun."""

    captured: dict[str, object] = {}

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        planned_tool_calls = kwargs["planned_tool_calls"]
        assert isinstance(planned_tool_calls, list)
        assert isinstance(planned_tool_calls[0], ToolCall)
        return TurnResult(
            run_id=3,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    envelope = ActionEnvelope(
        action="request_secure_field",
        message="secure",
        question_id="secure-1",
        secure_field="telegram_token",
        spec_patch={
            "tool_name": "app.run",
            "tool_params": {
                "app_name": "telegram",
                "action": "send_message",
            },
            "secure_nonce": "nonce-1",
        },
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    result = await resume_chat_after_secure_submit(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
    )

    assert result.envelope.action == "finalize"
    assert captured["message"] == "secure_resume:app.run"


async def test_resume_chat_after_secure_submit_without_tool_uses_synthetic_resume(
    monkeypatch,
) -> None:
    """Secure submit resume should fall back to synthetic continuation when no tool payload exists."""

    captured: dict[str, object] = {}

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.update(kwargs)
        return TurnResult(
            run_id=4,
            session_id="api-s",
            profile_id="default",
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    envelope = ActionEnvelope(
        action="request_secure_field",
        message="secure",
        question_id="secure-2",
        secure_field="telegram_token",
        spec_patch={"secure_nonce": "nonce-2"},
    )

    async def _resolve_trusted(**_: object) -> ActionEnvelope:
        return envelope

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_chat_turn", _fake_run_chat_turn)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_trusted,
    )

    result = await resume_chat_after_secure_submit(
        envelope=envelope,
        profile_id="default",
        session_id="api-s",
    )

    assert result.envelope.action == "finalize"
    assert isinstance(captured["message"], str)
    assert str(captured["message"]).startswith("secure_resume: a required credential was captured")
    assert captured["planned_tool_calls"] is None


async def test_resume_chat_interaction_blocks_when_question_not_trusted(monkeypatch) -> None:
    """Resume should reject unknown question ids instead of trusting client payload."""

    async def _resolve_none(**_: object) -> None:
        return None

    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime._resolve_trusted_pending_envelope",
        _resolve_none,
    )

    result = await resume_chat_interaction(
        envelope=ActionEnvelope(
            action="ask_question",
            message="confirm",
            question_id="approval-1",
            spec_patch={"tool_name": "danger.exec", "tool_params": {"x": 1}},
        ),
        profile_id="default",
        session_id="api-s",
        approved=True,
    )

    assert result.envelope.action == "block"
    assert result.envelope.blocked_reason == "interactive_question_invalid"


async def test_trusted_pending_resume_uses_internal_raw_patch_not_sanitized_runlog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Trusted resume should load raw replay params from internal storage, not sanitized runlog."""

    class _NoopMemoryRuntime:
        async def auto_save_turn(self, **kwargs: object) -> None:
            _ = kwargs

    class _NoopSessionCompaction:
        async def refresh_if_needed(self, **kwargs: object) -> object:
            _ = kwargs
            return SimpleNamespace(updated=False)

    class _NoopSessionRetention:
        async def garbage_collect_session(self, **kwargs: object) -> object:
            _ = kwargs
            return SimpleNamespace(deleted_turn_count=0, scanned_session_count=0)

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "api_runtime_trusted_resume_raw_patch.db")
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime.get_api_session_factory",
        lambda: factory,
    )
    question_id = "approval:raw-token"
    raw_secret = "tok_live_1234567890abcdefTOKEN"

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="api-s", profile_id="default")
        run_repo = RunRepository(session)
        runlog_repo = RunlogRepository(session)
        run = await run_repo.create_run(session_id="api-s", profile_id="default")

        async def _log_event(
            *,
            run_id: int,
            session_id: str,
            event_type: str,
            payload: dict[str, object],
        ) -> None:
            await runlog_repo.create_event(
                run_id=run_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
            )

        finalizer = TurnFinalizer(
            run_repo=run_repo,
            pending_resume_repo=PendingResumeEnvelopeRepository(session),
            pending_secure_repo=PendingSecureRequestRepository(session),
            memory_runtime=_NoopMemoryRuntime(),
            session_compaction=_NoopSessionCompaction(),
            session_retention=_NoopSessionRetention(),
            log_event=_log_event,
            sanitize_value=sanitize_value,
            secure_request_ttl_sec=300,
        )

        # Act
        await finalizer.finalize_pending_envelope(
            run_id=run.id,
            session_id="api-s",
            profile_id="default",
            user_message="approve credential write",
            machine_state="waiting_for_user",
            envelope=ActionEnvelope(
                action="ask_question",
                message="confirm",
                question_id=question_id,
                spec_patch={
                    "tool_name": "credentials.create",
                    "tool_params": {
                        "integration_name": "telegram",
                        "credential_name": "bot_token",
                        "token_value": raw_secret,
                    },
                },
            ),
        )

    async with session_scope(factory) as session:
        events = await RunlogRepository(session).list_session_events_by_type(
            profile_id="default",
            session_id="api-s",
            event_type="turn.ask_question",
            limit=10,
        )

    trusted = await _resolve_trusted_pending_envelope(
        profile_id="default",
        session_id="api-s",
        question_id=question_id,
        action="ask_question",
    )

    # Assert
    assert events
    assert raw_secret not in events[0].payload_json
    assert "[REDACTED]" in events[0].payload_json
    assert trusted is not None
    assert trusted.spec_patch is not None
    tool_params = trusted.spec_patch.get("tool_params")
    assert isinstance(tool_params, dict)
    assert tool_params["token_value"] == raw_secret

    await engine.dispose()
