"""Tests for detached subagent worker launch wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

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
