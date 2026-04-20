"""Tests for runlog runtime progress coalescing and cancellation throttling."""

from __future__ import annotations

import asyncio

import pytest

from afkbot.services.agent_loop.runlog_runtime import RunlogRuntime


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self._in_transaction = True

    async def commit(self) -> None:
        self.commit_calls += 1
        self._in_transaction = False

    def in_transaction(self) -> bool:
        return self._in_transaction

    def mark_transaction_open(self) -> None:
        self._in_transaction = True


class _FakeRunRepo:
    def __init__(self, *, cancel_requested: bool = False) -> None:
        self.cancel_requested = cancel_requested
        self.calls: list[int] = []

    async def is_cancel_requested(self, run_id: int) -> bool:
        self.calls.append(run_id)
        return self.cancel_requested


class _FakeRunlogRepo:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def create_event(self, **kwargs: object) -> None:
        self.events.append(dict(kwargs))


def _build_runtime(
    *,
    session: _FakeSession | None = None,
    run_repo: _FakeRunRepo | None = None,
    runlog_repo: _FakeRunlogRepo | None = None,
) -> tuple[RunlogRuntime, _FakeSession, _FakeRunRepo, _FakeRunlogRepo]:
    fake_session = session or _FakeSession()
    fake_run_repo = run_repo or _FakeRunRepo()
    fake_runlog_repo = runlog_repo or _FakeRunlogRepo()
    runtime = RunlogRuntime(
        session=fake_session,  # type: ignore[arg-type]
        run_repo=fake_run_repo,  # type: ignore[arg-type]
        runlog_repo=fake_runlog_repo,  # type: ignore[arg-type]
        sanitize_value=lambda value: value,
        to_payload_dict=lambda value: value if isinstance(value, dict) else {},
    )
    return runtime, fake_session, fake_run_repo, fake_runlog_repo


@pytest.mark.asyncio
async def test_runlog_runtime_dedupes_same_canonical_progress_stage_per_iteration() -> None:
    """Canonical duplicate progress writes inside one iteration should be skipped."""

    runtime, session, _run_repo, runlog_repo = _build_runtime()

    await runtime.log_progress(
        run_id=7,
        session_id="s-1",
        stage="llm_iteration",
        iteration=1,
    )
    session.mark_transaction_open()
    await runtime.log_progress(
        run_id=7,
        session_id="s-1",
        stage="thinking",
        iteration=1,
    )
    await runtime.log_progress(
        run_id=7,
        session_id="s-1",
        stage="planning",
        iteration=1,
    )
    session.mark_transaction_open()
    await runtime.log_progress(
        run_id=7,
        session_id="s-1",
        stage="thinking",
        iteration=2,
    )

    assert session.commit_calls == 3
    assert [event["event_type"] for event in runlog_repo.events] == [
        "turn.progress",
        "turn.progress",
        "turn.progress",
    ]
    assert [event["payload"] for event in runlog_repo.events] == [
        {"stage": "llm_iteration", "iteration": 1},
        {"stage": "planning", "iteration": 1},
        {"stage": "thinking", "iteration": 2},
    ]


@pytest.mark.asyncio
async def test_runlog_runtime_throttles_cancel_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated hot-loop cancellation probes should not hit storage every time."""

    clock = {"value": 100.0}
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runlog_runtime.time.monotonic",
        lambda: clock["value"],
    )
    runtime, session, run_repo, _runlog_repo = _build_runtime()

    await runtime.raise_if_cancel_requested(run_id=9)
    session.mark_transaction_open()
    clock["value"] += 0.02
    await runtime.raise_if_cancel_requested(run_id=9)
    clock["value"] += 0.05
    await runtime.raise_if_cancel_requested(run_id=9)

    assert session.commit_calls == 2
    assert run_repo.calls == [9, 9]


@pytest.mark.asyncio
async def test_runlog_runtime_raises_cancelled_error_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-process cancellation should still propagate on the next storage probe."""

    clock = {"value": 200.0}
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runlog_runtime.time.monotonic",
        lambda: clock["value"],
    )
    runtime, _session, _run_repo, _runlog_repo = _build_runtime(
        run_repo=_FakeRunRepo(cancel_requested=True),
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.raise_if_cancel_requested(run_id=3)
