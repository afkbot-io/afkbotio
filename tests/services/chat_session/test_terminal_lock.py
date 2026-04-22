"""Tests for exclusive terminal locks used by interactive CLI chat sessions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from afkbot.services.chat_session.terminal_lock import (
    ChatSessionTerminalLock,
    ChatSessionTerminalLockedError,
)


def test_chat_session_terminal_lock_rejects_concurrent_same_process_acquire(tmp_path: Path) -> None:
    """Second acquire in the same process should fail immediately."""

    lock = ChatSessionTerminalLock(root_dir=tmp_path)
    entered = Event()
    release = Event()

    def _hold_lock() -> None:
        with lock.acquire(profile_id="default", session_id="cli:default:incident-room"):
            entered.set()
            assert release.wait(timeout=5.0)

    holder = Thread(target=_hold_lock)
    holder.start()
    assert entered.wait(timeout=1.0)

    with pytest.raises(ChatSessionTerminalLockedError, match="incident-room"):
        with lock.acquire(profile_id="default", session_id="cli:default:incident-room"):
            pass

    release.set()
    holder.join(timeout=5.0)
    assert not holder.is_alive()

    with lock.acquire(profile_id="default", session_id="cli:default:incident-room"):
        pass


def test_chat_session_terminal_lock_rejects_cross_process_acquire(tmp_path: Path) -> None:
    """Another process must not be able to open the same chat session concurrently."""

    script = """
import sys
from pathlib import Path
from afkbot.services.chat_session.terminal_lock import ChatSessionTerminalLock

root_dir = Path(sys.argv[1])
ready_file = Path(sys.argv[2])
lock = ChatSessionTerminalLock(root_dir=root_dir)
with lock.acquire(profile_id="default", session_id="cli:default:incident-room"):
    ready_file.write_text("locked", encoding="utf-8")
    input()
"""
    ready_file = tmp_path / "chat-session-lock.signal"
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script, str(tmp_path), str(ready_file)],
        cwd=str(Path(__file__).resolve().parents[4]),
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = 3.0
        while not ready_file.exists():
            return_code = proc.poll()
            if return_code is not None:
                stderr_output = ""
                if proc.stderr is not None:
                    stderr_output = proc.stderr.read()
                pytest.fail(
                    "Child process exited before chat session lock handshake "
                    f"(returncode={return_code}, stderr={stderr_output!r})"
                )
            deadline -= 0.01
            if deadline <= 0:
                pytest.fail(
                    "Timed out waiting for child chat session lock handshake "
                    f"(lock_file={ready_file}, returncode={proc.poll()})"
                )
            import time

            time.sleep(0.01)

        lock = ChatSessionTerminalLock(root_dir=tmp_path)
        with pytest.raises(ChatSessionTerminalLockedError, match="incident-room"):
            with lock.acquire(profile_id="default", session_id="cli:default:incident-room"):
                pass
    finally:
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.write("\n")
            proc.stdin.flush()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
