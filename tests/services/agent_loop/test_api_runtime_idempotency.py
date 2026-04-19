"""Tests for API runtime turn idempotency behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.models.chat_turn_idempotency import ChatTurnIdempotencyClaim
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.api_runtime import (
    get_api_session_factory,
    initialize_api_runtime,
    poll_chat_progress,
    run_chat_turn,
    shutdown_api_runtime,
)
from afkbot.services.agent_loop.api_runtime_support import _idempotency_wait_poll_delay
from afkbot.services.agent_loop.progress_stream import ProgressCursor
from tests.services.agent_loop._loop_harness import create_test_db
from afkbot.settings import Settings
from afkbot.services.subagents import get_subagent_service


async def _prepare_api_runtime_db(
    *,
    factory,
    profile_id: str = "default",
    session_id: str = "api-s",
) -> None:
    """Seed the minimal profile/session rows required by API idempotency tests."""

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default(profile_id)
        await ChatSessionRepository(session).create(session_id=session_id, profile_id=profile_id)


async def _create_result_with_run(
    *,
    factory,
    profile_id: str,
    session_id: str,
    message: str,
) -> TurnResult:
    """Create a persisted run row and return a matching deterministic TurnResult."""

    async with session_scope(factory) as session:
        run = await RunRepository(session).create_run(
            session_id=session_id,
            profile_id=profile_id,
        )
    return TurnResult(
        run_id=run.id,
        profile_id=profile_id,
        session_id=session_id,
        envelope=ActionEnvelope(action="finalize", message=message),
    )


@pytest.mark.asyncio
async def test_shutdown_api_runtime_resets_only_current_root_subagent_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """API runtime shutdown should dispose only the subagent service for the active root."""

    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    settings_a = Settings(
        db_url=f"sqlite+aiosqlite:///{root_a / 'api-runtime-a.db'}",
        root_dir=root_a,
    )
    settings_b = Settings(
        db_url=f"sqlite+aiosqlite:///{root_b / 'api-runtime-b.db'}",
        root_dir=root_b,
    )
    service_a = get_subagent_service(settings_a)
    service_b = get_subagent_service(settings_b)
    assert service_a is not service_b

    shutdown_calls: list[str] = []

    async def _shutdown_a() -> None:
        shutdown_calls.append("a")

    async def _shutdown_b() -> None:
        shutdown_calls.append("b")

    browser_roots: list[Path] = []

    monkeypatch.setattr(service_a, "shutdown", _shutdown_a)
    monkeypatch.setattr(service_b, "shutdown", _shutdown_b)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings_b)

    class _FakeBrowserManager:
        async def close_all_for_root(self, *, root_dir: Path) -> None:
            browser_roots.append(root_dir)

    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime.get_browser_session_manager",
        lambda: _FakeBrowserManager(),
    )

    await initialize_api_runtime(settings=settings_a)
    await shutdown_api_runtime()

    assert shutdown_calls == ["a"]
    assert browser_roots == [settings_a.root_dir]
    assert get_subagent_service(settings_b) is service_b
    replacement_a = get_subagent_service(settings_a)
    assert replacement_a is not service_a

    await replacement_a.shutdown()
    await service_b.shutdown()


@pytest.mark.asyncio
async def test_shutdown_api_runtime_keeps_state_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failed shutdown should leave runtime state available for a later retry."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'api-runtime-cleanup-retry.db'}",
        root_dir=tmp_path,
    )
    attempts: list[str] = []

    class _FlakyBrowserManager:
        def __init__(self) -> None:
            self._fail = True

        async def close_all_for_root(self, *, root_dir: Path) -> None:
            attempts.append(str(root_dir))
            if self._fail:
                self._fail = False
                raise RuntimeError("browser cleanup failed")

    manager = _FlakyBrowserManager()
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime.get_browser_session_manager",
        lambda: manager,
    )

    await initialize_api_runtime(settings=settings)
    with pytest.raises(RuntimeError, match="browser cleanup failed"):
        await shutdown_api_runtime()

    assert get_api_session_factory() is not None

    await shutdown_api_runtime()

    assert attempts == [str(settings.root_dir), str(settings.root_dir)]
    assert get_api_session_factory() is None


@pytest.mark.asyncio
async def test_run_chat_turn_reuses_result_for_same_client_msg_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated client id should return stored response without rerunning turn."""

    settings, engine, factory = await create_test_db(tmp_path, "api-runtime-idempotency.db")
    await _prepare_api_runtime_db(factory=factory)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_api_session_factory", lambda: factory)

    calls = {"count": 0}

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls: list[object] | None = None,
        progress_sink: object | None = None,
        **_unused: object,
    ) -> TurnResult:
        _ = planned_tool_calls, progress_sink
        calls["count"] += 1
        return await _create_result_with_run(
            factory=factory,
            profile_id=profile_id,
            session_id=session_id,
            message=f"ok-{message}-{calls['count']}",
        )

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_once_result", _fake_run_once_result)

    try:
        first = await run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-1",
        )
        second = await run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-1",
        )
    finally:
        await engine.dispose()

    assert calls["count"] == 1
    assert second.run_id == first.run_id
    assert second.envelope.message == first.envelope.message


@pytest.mark.asyncio
async def test_run_chat_turn_reclaims_stale_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stale idempotency claims should not block future execution forever."""

    settings, engine, factory = await create_test_db(tmp_path, "api-runtime-idempotency-stale.db")
    await _prepare_api_runtime_db(factory=factory)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_api_session_factory", lambda: factory)
    try:
        async with session_scope(factory) as db:
            db.add(
                ChatTurnIdempotencyClaim(
                    profile_id="default",
                    session_id="api-s",
                    client_msg_id="msg-stale",
                    owner_token="stale-owner",
                    created_at=datetime.now(UTC) - timedelta(minutes=10),
                    updated_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await db.flush()

        calls = {"count": 0}

        async def _fake_run_once_result(
            *,
            message: str,
            profile_id: str,
            session_id: str,
            planned_tool_calls: list[object] | None = None,
            progress_sink: object | None = None,
            **_unused: object,
        ) -> TurnResult:
            _ = message, planned_tool_calls, progress_sink
            calls["count"] += 1
            return await _create_result_with_run(
                factory=factory,
                profile_id=profile_id,
                session_id=session_id,
                message="ok-stale",
            )

        monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_once_result", _fake_run_once_result)

        result = await run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-stale",
        )
    finally:
        await engine.dispose()

    assert calls["count"] == 1
    assert result.envelope.message == "ok-stale"


@pytest.mark.asyncio
async def test_run_chat_turn_runs_again_for_different_client_msg_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Different idempotency keys should execute separate runtime turns."""

    settings, engine, factory = await create_test_db(tmp_path, "api-runtime-idempotency-diff.db")
    await _prepare_api_runtime_db(factory=factory)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_api_session_factory", lambda: factory)

    calls = {"count": 0}

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls: list[object] | None = None,
        progress_sink: object | None = None,
        **_unused: object,
    ) -> TurnResult:
        _ = message, planned_tool_calls, progress_sink
        calls["count"] += 1
        return await _create_result_with_run(
            factory=factory,
            profile_id=profile_id,
            session_id=session_id,
            message=f"ok-{calls['count']}",
        )

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_once_result", _fake_run_once_result)

    try:
        first = await run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-1",
        )
        second = await run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-2",
        )
    finally:
        await engine.dispose()

    assert calls["count"] == 2
    assert second.run_id != first.run_id


@pytest.mark.asyncio
async def test_run_chat_turn_parallel_same_key_executes_side_effects_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Parallel same-key requests should share one execution through the real orchestrator path."""

    settings, engine, factory = await create_test_db(tmp_path, "api-runtime-idempotency-parallel.db")
    await _prepare_api_runtime_db(factory=factory)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_api_session_factory", lambda: factory)

    calls = {"count": 0}
    entered = asyncio.Event()
    release = asyncio.Event()

    def _fake_build_agent_loop_runner(self, session, profile_id):  # type: ignore[no-untyped-def]
        _ = self, session, profile_id

        class _FakeRunner:
            async def run_turn(
                self,
                *,
                message: str,
                profile_id: str,
                session_id: str,
                planned_tool_calls: list[object] | None = None,
                context_overrides: object | None = None,
            ) -> TurnResult:
                _ = message, planned_tool_calls, context_overrides
                calls["count"] += 1
                entered.set()
                await release.wait()
                return await _create_result_with_run(
                    factory=factory,
                    profile_id=profile_id,
                    session_id=session_id,
                    message=f"ok-{calls['count']}",
                )

        return _FakeRunner()

    monkeypatch.setattr(
        "afkbot.services.session_orchestration.service.SessionOrchestrator._build_agent_loop_runner",
        _fake_build_agent_loop_runner,
    )

    first_task = asyncio.create_task(
        run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-parallel",
        )
    )
    await entered.wait()
    second_task = asyncio.create_task(
        run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-parallel",
        )
    )

    try:
        await asyncio.sleep(0.05)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)
    finally:
        await engine.dispose()

    assert calls["count"] == 1
    assert second.run_id == first.run_id
    assert second.envelope.message == first.envelope.message


@pytest.mark.asyncio
async def test_run_chat_turn_does_not_reclaim_live_claim_with_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Heartbeat should keep one long-running claim live and prevent duplicate execution."""

    settings, engine, factory = await create_test_db(tmp_path, "api-runtime-idempotency-heartbeat.db")
    await _prepare_api_runtime_db(factory=factory)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_api_session_factory", lambda: factory)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime._IDEMPOTENCY_HEARTBEAT_SEC", 0.01)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime.idempotency_claim_cutoff_support",
        lambda *, settings: datetime.now(UTC) - timedelta(milliseconds=20),
    )

    calls = {"count": 0}
    entered = asyncio.Event()

    async def _fake_run_once_result(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls: list[object] | None = None,
        progress_sink: object | None = None,
        **_unused: object,
    ) -> TurnResult:
        _ = message, planned_tool_calls, progress_sink
        calls["count"] += 1
        entered.set()
        await asyncio.sleep(0.08)
        return await _create_result_with_run(
            factory=factory,
            profile_id=profile_id,
            session_id=session_id,
            message=f"ok-{calls['count']}",
        )

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.run_once_result", _fake_run_once_result)

    first_task = asyncio.create_task(
        run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-heartbeat",
        )
    )
    await entered.wait()
    await asyncio.sleep(0.03)
    second_task = asyncio.create_task(
        run_chat_turn(
            message="hello",
            profile_id="default",
            session_id="api-s",
            client_msg_id="msg-heartbeat",
        )
    )

    try:
        first, second = await asyncio.gather(first_task, second_task)
    finally:
        await engine.dispose()

    assert calls["count"] == 1
    assert second.run_id == first.run_id
    assert second.envelope.message == first.envelope.message


@pytest.mark.asyncio
async def test_poll_chat_progress_uses_initialized_runtime_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Shared API runtime should avoid per-poll engine bootstrap after initialization."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'api-runtime-shared.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    await initialize_api_runtime(settings=settings)
    try:
        monkeypatch.setattr(
            "afkbot.services.agent_loop.api_runtime.create_engine",
            lambda _settings: (_ for _ in ()).throw(AssertionError("create_engine must not be called")),
        )
        response = await poll_chat_progress(
            profile_id="default",
            session_id="api-s",
            cursor=ProgressCursor(run_id=None, last_event_id=0),
        )
    finally:
        await shutdown_api_runtime()

    assert response.events == []
    assert response.cursor.run_id is None
    assert response.cursor.last_event_id == 0


def test_idempotency_wait_poll_delay_uses_capped_backoff() -> None:
    """Idempotency wait loop should widen polls under prolonged contention."""

    assert _idempotency_wait_poll_delay(0) == pytest.approx(0.05)
    assert _idempotency_wait_poll_delay(1) == pytest.approx(0.1)
    assert _idempotency_wait_poll_delay(2) == pytest.approx(0.2)
    assert _idempotency_wait_poll_delay(3) == pytest.approx(0.4)
    assert _idempotency_wait_poll_delay(4) == pytest.approx(0.5)
    assert _idempotency_wait_poll_delay(8) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_shutdown_api_runtime_resets_subagent_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """API shutdown should reset the current-root subagent runtime before tearing down process resources."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'api-runtime-subagents.db'}",
        root_dir=tmp_path,
    )
    calls: dict[str, object] = {}

    class _FakeBrowserManager:
        async def close_all_for_root(self, *, root_dir: Path) -> None:
            calls["browser_root"] = root_dir

    async def _fake_reset_subagent_service_for_root_async(*, settings: Settings) -> None:
        calls["subagents_reset_root"] = settings.root_dir

    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime.get_browser_session_manager",
        lambda: _FakeBrowserManager(),
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.api_runtime.reset_subagent_service_for_root_async",
        _fake_reset_subagent_service_for_root_async,
    )

    await initialize_api_runtime(settings=settings)
    await shutdown_api_runtime()

    assert calls["subagents_reset_root"] == settings.root_dir
    assert calls["browser_root"] == settings.root_dir
