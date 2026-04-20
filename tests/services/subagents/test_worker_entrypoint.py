"""Tests for detached subagent worker launch wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path
import signal
import subprocess

from pytest import MonkeyPatch

from afkbot.services.subagents.launcher import SubagentLauncher
from afkbot.settings import Settings


def test_process_launcher_uses_dedicated_worker_module(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Process launcher should spawn detached workers via dedicated module entrypoint."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}", root_dir=tmp_path)
    launcher = SubagentLauncher(settings=settings, launch_mode="process")
    captured: dict[str, object] = {}

    class _DummyProc:  # pragma: no cover - pure test stub
        pass

    def _fake_popen(command: list[str], **kwargs: object) -> _DummyProc:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _DummyProc()

    monkeypatch.setattr("afkbot.services.subagents.runtime_support.subprocess.Popen", _fake_popen)

    async def _unused_execute_inline(task_id: str) -> bool:
        _ = task_id
        return True

    launcher.spawn(task_id="task-1", execute_inline=_unused_execute_inline)

    command = captured["command"]
    assert isinstance(command, list)
    assert "afkbot.workers.subagent_worker" in command
    assert "--task-id" in command
    assert "task-1" in command


async def test_inline_launcher_shutdown_cancels_running_tasks(tmp_path: Path) -> None:
    """Inline launcher shutdown should not hang behind a still-running subagent task."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}", root_dir=tmp_path)
    launcher = SubagentLauncher(settings=settings, launch_mode="inline")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _blocking_execute_inline(task_id: str) -> bool:
        _ = task_id
        started.set()
        try:
            await asyncio.Event().wait()
            return True
        except asyncio.CancelledError:
            cancelled.set()
            raise

    launcher.spawn(task_id="task-1", execute_inline=_blocking_execute_inline)
    await started.wait()

    await asyncio.wait_for(launcher.shutdown(), timeout=1)

    assert cancelled.is_set()


async def test_process_launcher_cancel_escalates_to_kill_after_term_timeout(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Process cancellation should escalate from TERM to KILL when worker does not exit."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'worker-cancel.db'}",
        root_dir=tmp_path,
        runtime_shutdown_timeout_sec=0.01,
    )
    launcher = SubagentLauncher(settings=settings, launch_mode="process")
    signals: list[tuple[int, signal.Signals]] = []

    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 1234
            self._poll_calls = 0
            self._done = False

        def poll(self) -> int | None:
            return 0 if self._done else None

        def wait(self, timeout: float | None = None) -> int:
            self._poll_calls += 1
            if self._poll_calls == 1:
                raise subprocess.TimeoutExpired(cmd="subagent", timeout=timeout)
            self._done = True
            return 0

        def terminate(self) -> None:
            signals.append((self.pid, signal.SIGTERM))

        def kill(self) -> None:
            signals.append((self.pid, signal.SIGKILL))

    monkeypatch.setattr(
        "afkbot.services.subagents.launcher.os.killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    launcher._processes["task-1"] = _FakeProc()  # noqa: SLF001

    await launcher.cancel(task_id="task-1")

    assert signals == [(1234, signal.SIGTERM), (1234, signal.SIGKILL)]
    assert launcher._processes == {}  # noqa: SLF001


async def test_process_launcher_cancel_with_zero_timeout_stays_bounded(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Zero shutdown timeout should skip the blocking TERM wait and escalate immediately."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'worker-cancel-zero.db'}",
        root_dir=tmp_path,
        runtime_shutdown_timeout_sec=0.0,
    )
    launcher = SubagentLauncher(settings=settings, launch_mode="process")
    signals: list[tuple[int, signal.Signals]] = []
    wait_calls: list[float | None] = []

    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 5678
            self._done = False

        def poll(self) -> int | None:
            return 0 if self._done else None

        def wait(self, timeout: float | None = None) -> int:
            wait_calls.append(timeout)
            if not self._done:
                raise subprocess.TimeoutExpired(cmd="subagent", timeout=timeout)
            return 0

        def terminate(self) -> None:
            signals.append((self.pid, signal.SIGTERM))

        def kill(self) -> None:
            signals.append((self.pid, signal.SIGKILL))
            self._done = True

    monkeypatch.setattr(
        "afkbot.services.subagents.launcher.os.killpg",
        lambda pid, sig: signals.append((pid, sig)) or setattr(launcher._processes["task-0"], "_done", sig == signal.SIGKILL),
    )

    launcher._processes["task-0"] = _FakeProc()  # noqa: SLF001

    await asyncio.wait_for(launcher.cancel(task_id="task-0"), timeout=1)

    assert signals == [(5678, signal.SIGTERM), (5678, signal.SIGKILL)]
    assert wait_calls == []
    assert launcher._processes == {}  # noqa: SLF001
