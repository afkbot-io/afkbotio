"""Focused tests for turn finalization persistence rules."""

from __future__ import annotations

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.turn_finalizer import TurnFinalizer


class _FakeRunRepo:
    def __init__(self) -> None:
        self.status_updates: list[tuple[int, str]] = []
        self.commit_count = 0

    async def update_status(self, run_id: int, status: str) -> None:
        self.status_updates.append((run_id, status))

    async def commit_pending(self) -> None:
        self.commit_count += 1


class _FakeTranscriptStore:
    def __init__(self) -> None:
        self.chat_turns: list[tuple[str, str, str, str]] = []

    async def create_turn(
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
    def __init__(self, *, run_repo: _FakeRunRepo | None = None) -> None:
        self._run_repo = run_repo

    async def refresh_if_needed(self, **kwargs: object):
        if self._run_repo is not None and self._run_repo.commit_count < 1:
            raise AssertionError("pending DB writes must commit before compaction")
        raise AssertionError("refresh_if_needed should not be called when persist_turn=False")


class _FakeRetention:
    async def garbage_collect_session(self, **kwargs: object):
        raise AssertionError("garbage_collect_session should not be called when persist_turn=False")


@pytest.mark.asyncio
async def test_finalize_pending_envelope_skips_persistence_when_turn_is_ephemeral() -> None:
    run_repo = _FakeRunRepo()
    transcript_store = _FakeTranscriptStore()
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
        transcript_store=transcript_store,  # type: ignore[arg-type]
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
    assert transcript_store.chat_turns == []
    assert pending_resume_repo.calls == []
    assert pending_secure_repo.calls == []
    assert logged_events == ["turn.request_secure_field"]


async def _log_event(events: list[str], payload: dict[str, object]) -> None:
    event_type = payload.get("event_type")
    if isinstance(event_type, str):
        events.append(event_type)


@pytest.mark.asyncio
async def test_finalize_turn_commits_pending_writes_before_compaction() -> None:
    run_repo = _FakeRunRepo()
    transcript_store = _FakeTranscriptStore()
    logged_events: list[str] = []

    class _Compaction:
        async def refresh_if_needed(self, **kwargs: object):
            assert run_repo.commit_count == 1
            return type(
                "Result",
                (),
                {
                    "updated": False,
                },
            )()

    class _Retention:
        async def garbage_collect_session(self, **kwargs: object):
            return type(
                "Result",
                (),
                {
                    "deleted_turn_count": 0,
                    "scanned_session_count": 1,
                },
            )()

    class _MemoryRuntime:
        async def auto_save_turn(self, **kwargs: object) -> None:
            return None

    finalizer = TurnFinalizer(
        run_repo=run_repo,  # type: ignore[arg-type]
        pending_resume_repo=_FakePendingRepo(),  # type: ignore[arg-type]
        pending_secure_repo=_FakePendingRepo(),  # type: ignore[arg-type]
        memory_runtime=_MemoryRuntime(),  # type: ignore[arg-type]
        session_compaction=_Compaction(),  # type: ignore[arg-type]
        session_retention=_Retention(),  # type: ignore[arg-type]
        transcript_store=transcript_store,  # type: ignore[arg-type]
        log_event=lambda **kwargs: _log_event(logged_events, kwargs),  # type: ignore[arg-type]
        sanitize_value=lambda value: value,
        secure_request_ttl_sec=900,
    )

    result = await finalizer.finalize_turn(
        run_id=7,
        session_id="s-commit-before-compaction",
        profile_id="default",
        user_message="u",
        assistant_message="a",
        action="finalize",
        blocked_reason=None,
        machine_state="finalize",
        policy=type("Policy", (), {})(),  # type: ignore[arg-type]
    )

    assert result.run_id == 7
    assert run_repo.status_updates == [(7, "completed")]
    assert run_repo.commit_count == 1
    assert logged_events == ["turn.finalize"]
