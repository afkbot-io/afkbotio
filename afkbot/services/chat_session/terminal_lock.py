"""Cross-process terminal lock preventing duplicate CLI chat sessions."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, get_ident
from typing import Iterator

try:  # pragma: no cover - import branch depends on platform
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


class ChatSessionTerminalLockedError(RuntimeError):
    """Raised when another terminal already owns one active CLI chat session."""

    def __init__(self, *, profile_id: str, session_id: str) -> None:
        reason = (
            f"Chat session '{session_id}' is already open in another terminal "
            f"for profile '{profile_id}'."
        )
        super().__init__(reason)
        self.error_code = "chat_session_terminal_locked"
        self.reason = reason
        self.profile_id = profile_id
        self.session_id = session_id


@dataclass(frozen=True)
class _ActiveSessionLock:
    fd: int
    depth: int
    owner_thread_id: int


class ChatSessionTerminalLock:
    """Coordinate in-process and cross-process ownership for one chat session."""

    def __init__(self, *, root_dir: Path) -> None:
        self._lock_dir = root_dir / ".afk" / "session_locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._index_lock = Lock()
        self._active_by_key: dict[tuple[str, str], _ActiveSessionLock] = {}

    @contextmanager
    def acquire(self, *, profile_id: str, session_id: str) -> Iterator[None]:
        """Acquire one non-blocking terminal lock or raise immediately."""

        key = _normalize_lock_key(profile_id=profile_id, session_id=session_id)
        self._acquire_nowait(key=key)
        try:
            yield
        finally:
            self._release(key=key)

    def _acquire_nowait(self, *, key: tuple[str, str]) -> None:
        with self._index_lock:
            active = self._active_by_key.get(key)
            if active is not None:
                if active.owner_thread_id != get_ident():
                    raise ChatSessionTerminalLockedError(profile_id=key[0], session_id=key[1])
                self._active_by_key[key] = _ActiveSessionLock(
                    fd=active.fd,
                    depth=active.depth + 1,
                    owner_thread_id=active.owner_thread_id,
                )
                return

            lock_fd = self._acquire_process_lock(key=key)
            self._active_by_key[key] = _ActiveSessionLock(fd=lock_fd, depth=1, owner_thread_id=get_ident())

    def _release(self, *, key: tuple[str, str]) -> None:
        fd: int | None = None
        with self._index_lock:
            active = self._active_by_key.get(key)
            if active is None:
                return
            if active.depth > 1:
                self._active_by_key[key] = _ActiveSessionLock(
                    fd=active.fd,
                    depth=active.depth - 1,
                    owner_thread_id=active.owner_thread_id,
                )
                return
            fd = active.fd
            self._active_by_key.pop(key, None)
        if fd is not None:
            _release_process_lock(fd)

    def _acquire_process_lock(self, *, key: tuple[str, str]) -> int:
        if fcntl is None:
            raise RuntimeError("Terminal session lock is unavailable on this platform.")

        profile_id, session_id = key
        lock_path = self._lock_dir / _lock_file_name(profile_id=profile_id, session_id=session_id)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"profile_id={profile_id}\nsession_id={session_id}\n".encode("utf-8"))
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise ChatSessionTerminalLockedError(profile_id=profile_id, session_id=session_id) from exc
        except Exception:
            os.close(fd)
            raise
        return fd


def _normalize_lock_key(*, profile_id: str, session_id: str) -> tuple[str, str]:
    normalized_profile = str(profile_id).strip()
    normalized_session = str(session_id).strip()
    if not normalized_profile:
        raise ValueError("profile_id is required")
    if not normalized_session:
        raise ValueError("session_id is required")
    return normalized_profile, normalized_session


def _lock_file_name(*, profile_id: str, session_id: str) -> str:
    token = hashlib.sha256(f"{profile_id}\0{session_id}".encode("utf-8")).hexdigest()[:32]
    return f"{token}.lock"


def _release_process_lock(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_CHAT_SESSION_TERMINAL_LOCKS_BY_ROOT: dict[str, ChatSessionTerminalLock] = {}
_CHAT_SESSION_TERMINAL_LOCKS_INDEX = Lock()


def get_chat_session_terminal_lock(*, root_dir: Path) -> ChatSessionTerminalLock:
    """Return one shared terminal lock instance for the current workspace root."""

    key = str(root_dir.resolve())
    with _CHAT_SESSION_TERMINAL_LOCKS_INDEX:
        lock = _CHAT_SESSION_TERMINAL_LOCKS_BY_ROOT.get(key)
        if lock is None:
            lock = ChatSessionTerminalLock(root_dir=root_dir)
            _CHAT_SESSION_TERMINAL_LOCKS_BY_ROOT[key] = lock
        return lock
