"""Tests for profile files single-writer lock."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from afkbot.services.policy import ProfileFilesLock, ProfileFilesLockedError
from afkbot.services.profile_id import InvalidProfileIdError


async def test_profile_files_lock_rejects_concurrent_writers(tmp_path: Path) -> None:
    """Second acquire on same profile should fail with deterministic error."""

    lock = ProfileFilesLock(root_dir=tmp_path)
    gate = asyncio.Event()

    async def _hold_lock() -> None:
        async with lock.acquire("p1"):
            gate.set()
            await asyncio.sleep(0.2)

    holder = asyncio.create_task(_hold_lock())
    await gate.wait()

    with pytest.raises(ProfileFilesLockedError, match="profile: p1"):
        async with lock.acquire("p1"):
            pass

    await holder


async def test_profile_files_lock_rejects_cross_process_writer(tmp_path: Path) -> None:
    """Second process should fail acquiring the same profile lock."""

    script = """
import asyncio
import sys
from pathlib import Path
from afkbot.services.policy import ProfileFilesLock

async def main() -> None:
    root_dir = Path(sys.argv[1])
    ready_file = Path(sys.argv[2])
    lock = ProfileFilesLock(root_dir=root_dir)
    async with lock.acquire("p1"):
        ready_file.write_text("locked", encoding="utf-8")
        await asyncio.Event().wait()

asyncio.run(main())
"""
    ready_file = tmp_path / "child-lock-acquired.signal"
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script, str(tmp_path), str(ready_file)],
        cwd=str(Path(__file__).resolve().parents[3]),
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = asyncio.get_running_loop().time() + 3.0
        while not ready_file.exists():
            return_code = proc.poll()
            if return_code is not None:
                stderr_output = ""
                if proc.stderr is not None:
                    stderr_output = proc.stderr.read()
                pytest.fail(
                    "Child process exited before lock acquire handshake "
                    f"(returncode={return_code}, stderr={stderr_output!r})"
                )
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(
                    "Timed out waiting for child lock acquire handshake "
                    f"(lock_file={ready_file}, returncode={proc.poll()})"
                )
            await asyncio.sleep(0.01)

        lock = ProfileFilesLock(root_dir=tmp_path)
        with pytest.raises(ProfileFilesLockedError, match="profile: p1"):
            async with lock.acquire("p1"):
                pass
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


async def test_profile_files_lock_rejects_invalid_profile_id_before_side_effects(
    tmp_path: Path,
) -> None:
    """Invalid profile id should fail before creating any lock file."""

    lock = ProfileFilesLock(root_dir=tmp_path)

    with pytest.raises(InvalidProfileIdError, match="Invalid profile id"):
        async with lock.acquire("bad/id"):
            pass

    profiles_root = tmp_path / "profiles"
    assert list(profiles_root.rglob("*.lock")) == []


async def test_profile_files_lock_rejects_traversal_before_writing_outside_locks(
    tmp_path: Path,
) -> None:
    """Traversal profile id must never create lock files outside `.locks`."""

    lock = ProfileFilesLock(root_dir=tmp_path)

    with pytest.raises(InvalidProfileIdError, match="Invalid profile id"):
        async with lock.acquire("../outside"):
            pass

    profiles_root = tmp_path / "profiles"
    outside_locks = [
        path for path in profiles_root.rglob("*.lock") if ".locks" not in path.parts
    ]

    assert outside_locks == []
    assert not (profiles_root / "outside.lock").exists()
