"""Focused tests for turn finalization persistence rules."""

from __future__ import annotations

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.turn_finalizer import TurnFinalizer


class _FakeRunRepo:
    def __init__(self) -> None:
        self.status_updates: list[tuple[int, str]] = []
        self.chat_turns: list[tuple[str, str, str, str]] = []

    async def update_status(self, run_id: int, status: str) -> None:
        self.status_updates.append((run_id, status))

    async def create_chat_turn(
        self,
        *,
        session_id: str,
        profile_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self.chat_turns.append((session_id, profile_id, user_message, assistant_message))


class _FakePendingRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _FakeMemoryRuntime:
    async def auto_save_turn(self, **kwargs: object) -> None:
        raise AssertionError("auto_save_turn should not be called in this test")


class _FakeCompaction:
    async def refresh_if_needed(self, **kwargs: object):
        raise AssertionError("refresh_if_needed should not be called when persist_turn=False")


class _FakeRetention:
    async def garbage_collect_session(self, **kwargs: object):
        raise AssertionError(
            "garbage_collect_session should not be called when persist_turn=False"
        )


@pytest.mark.asyncio
async def test_finalize_pending_envelope_skips_persistence_when_turn_is_ephemeral() -> None:
    run_repo = _FakeRunRepo()
    pending_resume_repo = _FakePendingRepo()
    pending_secure_repo = _FakePendingRepo()
    logged_events: list[str] = []
    finalizer = TurnFinalizer(
        run_repo=run_repo,
        pending_resume_repo=pending_resume_repo,  # type: ignore[arg-type]
        pending_secure_repo=pending_secure_repo,  # type: ignore[arg-type]
        memory_runtime=_FakeMemoryRuntime(),  # type: ignore[arg-type]
        session_compaction=_FakeCompaction(),  # type: ignore[arg-type]
        session_retention=_FakeRetention(),  # type: ignore[arg-type]
        log_event=lambda **kwargs: _log_event(logged_events, kwargs),  # type: ignore[arg-type]
        sanitize_value=lambda value: value,
        secure_request_ttl_sec=900,
    )

    envelope = ActionEnvelope(
        action="request_secure_field",
        message="Need API token",
        question_id="q-1",
        secure_field="api_token",
        spec_patch={
            "integration_name": "github",
            "credential_name": "token",
            "credential_profile_key": "default",
            "secure_nonce": "nonce-1",
        },
    )

    result = await finalizer.finalize_pending_envelope(
        run_id=1,
        session_id="s-ephemeral",
        profile_id="default",
        user_message="plan this",
        machine_state="plan",
        envelope=envelope,
        persist_turn=False,
    )

    assert result.envelope.message == "Need API token"
    assert run_repo.status_updates == [(1, "completed")]
    assert run_repo.chat_turns == []
    assert pending_resume_repo.calls == []
    assert pending_secure_repo.calls == []
    assert logged_events == ["turn.request_secure_field"]


async def _log_event(events: list[str], payload: dict[str, object]) -> None:
    event_type = payload.get("event_type")
    if isinstance(event_type, str):
        events.append(event_type)
