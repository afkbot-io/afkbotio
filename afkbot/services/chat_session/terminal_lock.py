"""Cross-process terminal lock preventing duplicate CLI chat sessions."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
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


class ChatSessionTerminalLock:
    """Non-blocking process lock keyed by `(profile_id, session_id)`."""

    def __init__(self, *, root_dir: Path) -> None:
        self._root_dir = root_dir.resolve()
        self._lock_dir = self._root_dir / ".locks" / "chat_sessions"
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._index_lock = Lock()
        self._held_keys: set[tuple[str, str]] = set()
        self._held_fds: dict[tuple[str, str], int] = {}

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
            if key in self._held_keys:
                raise ChatSessionTerminalLockedError(profile_id=key[0], session_id=key[1])
            lock_fd = self._acquire_process_lock(key=key)
            self._held_keys.add(key)
            self._held_fds[key] = lock_fd

    def _release(self, *, key: tuple[str, str]) -> None:
        with self._index_lock:
            self._held_keys.discard(key)
            fd = self._held_fds.pop(key, None)
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
