"""In-process single-writer lock for profile-scoped files."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from afkbot.services.policy.contracts import ProfileFilesLockedError
from afkbot.services.profile_id import validate_profile_id

try:  # pragma: no cover - import branch depends on platform
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


class ProfileFilesLock:
    """Single-writer lock keyed by profile id with cross-process support."""

    def __init__(self, *, root_dir: Path) -> None:
        self._root_dir = root_dir.resolve()
        self._lock_dir = self._root_dir / "profiles" / ".locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._index_lock = asyncio.Lock()
        self._held_profiles: set[str] = set()
        self._held_fds: dict[str, int] = {}

    @asynccontextmanager
    async def acquire(self, profile_id: str) -> AsyncIterator[None]:
        """Acquire non-blocking writer lock for profile file mutation."""

        await self._acquire_nowait(profile_id=profile_id)
        try:
            yield
        finally:
            await self._release(profile_id=profile_id)

    async def _acquire_nowait(self, *, profile_id: str) -> None:
        validate_profile_id(profile_id)
        async with self._index_lock:
            if profile_id in self._held_profiles:
                raise ProfileFilesLockedError(profile_id=profile_id)
            lock_fd = self._acquire_process_lock(profile_id=profile_id)
            self._held_profiles.add(profile_id)
            self._held_fds[profile_id] = lock_fd

    async def _release(self, *, profile_id: str) -> None:
        async with self._index_lock:
            self._held_profiles.discard(profile_id)
            fd = self._held_fds.pop(profile_id, None)
            if fd is not None:
                self._release_process_lock(fd)

    def _acquire_process_lock(self, *, profile_id: str) -> int:
        lock_path = self._lock_dir / f"{profile_id}.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is None:
            return fd
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError as exc:
            os.close(fd)
            raise ProfileFilesLockedError(profile_id=profile_id) from exc
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def _release_process_lock(fd: int) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


_PROFILE_FILES_LOCKS_BY_ROOT: dict[str, ProfileFilesLock] = {}
_PROFILE_FILES_LOCKS_INDEX = Lock()


def get_profile_files_lock(*, root_dir: Path) -> ProfileFilesLock:
    """Return shared process-local profile files lock instance for root."""

    key = str(root_dir.resolve())
    with _PROFILE_FILES_LOCKS_INDEX:
        lock = _PROFILE_FILES_LOCKS_BY_ROOT.get(key)
        if lock is None:
            lock = ProfileFilesLock(root_dir=root_dir)
            _PROFILE_FILES_LOCKS_BY_ROOT[key] = lock
        return lock
