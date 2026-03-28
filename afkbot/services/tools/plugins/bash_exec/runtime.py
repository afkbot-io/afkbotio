"""Interactive process runtime for resumable `bash.exec` sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
import signal
from pathlib import Path
from typing import Final
from uuid import uuid4

_POST_EXIT_GRACE_SEC: Final[float] = 0.1


def terminate_process_tree(pid: int | None, sig: int) -> None:
    """Terminate one process group when possible, or fall back to the process pid."""

    if pid is None or pid <= 0:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        os.kill(pid, sig)
    except OSError:
        return


@dataclass(frozen=True)
class BashExecSessionStartRequest:
    """Spawn contract for one interactive `bash.exec` session."""

    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    display_cmd: str
    cwd_label: str
    env_keys: tuple[str, ...]
    shell: str
    login_requested: bool
    login_applied: bool
    redacted_values: frozenset[str]


@dataclass(frozen=True)
class BashExecSessionResult:
    """Bounded per-call output chunk for one interactive `bash.exec` session."""

    display_cmd: str
    cwd_label: str
    env_keys: tuple[str, ...]
    shell: str
    login_requested: bool
    login_applied: bool
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    exit_code: int | None
    session_id: str | None
    running: bool
    chars_written: int = 0
    redacted_values: frozenset[str] = frozenset()


@dataclass(slots=True)
class _DrainSnapshot:
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_closed: bool
    stderr_closed: bool
    exit_code: int | None
    version: int


@dataclass(slots=True)
class _BashExecSession:
    session_id: str
    process: asyncio.subprocess.Process
    display_cmd: str
    cwd_label: str
    env_keys: tuple[str, ...]
    shell: str
    login_requested: bool
    login_applied: bool
    redacted_values: frozenset[str]
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    stdout_buffer: bytearray = field(default_factory=bytearray)
    stderr_buffer: bytearray = field(default_factory=bytearray)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_closed: bool = False
    stderr_closed: bool = False
    exit_code: int | None = None
    version: int = 0
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class BashExecSessionManager:
    """Manage in-memory interactive shell sessions for one tool registry instance."""

    def __init__(self, *, max_buffer_bytes: int) -> None:
        self._max_buffer_bytes = max(1, max_buffer_bytes)
        self._sessions: dict[str, _BashExecSession] = {}
        self._lock = asyncio.Lock()

    async def start_session(
        self,
        *,
        request: BashExecSessionStartRequest,
        yield_time_ms: int,
    ) -> BashExecSessionResult:
        """Spawn one process and return a bounded first output chunk."""

        process = await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=str(request.cwd),
            env=request.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        session = _BashExecSession(
            session_id=self._new_session_id(),
            process=process,
            display_cmd=request.display_cmd,
            cwd_label=request.cwd_label,
            env_keys=request.env_keys,
            shell=request.shell,
            login_requested=request.login_requested,
            login_applied=request.login_applied,
            redacted_values=request.redacted_values,
        )
        self._attach_background_tasks(session)
        async with self._lock:
            self._sessions[session.session_id] = session
        try:
            return await self._collect_result(
                session=session,
                yield_time_ms=yield_time_ms,
                chars_written=0,
            )
        except asyncio.CancelledError:
            await self._discard_session(session.session_id, terminate=True)
            raise

    async def resume_session(
        self,
        *,
        session_id: str,
        chars: str,
        yield_time_ms: int,
    ) -> BashExecSessionResult:
        """Write to one existing session stdin and collect the next bounded output chunk."""

        session = await self._get_session(session_id)
        stdin = session.process.stdin
        if chars:
            if stdin is None or stdin.is_closing():
                await self._discard_session(session_id, terminate=False)
                raise ValueError(f"stdin is closed for session_id={session_id}")
            try:
                stdin.write(chars.encode("utf-8"))
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                await self._discard_session(session_id, terminate=False)
                raise ValueError(f"stdin is closed for session_id={session_id}") from exc
        try:
            return await self._collect_result(
                session=session,
                yield_time_ms=yield_time_ms,
                chars_written=len(chars),
            )
        except asyncio.CancelledError:
            await self._discard_session(session_id, terminate=True)
            raise

    async def _collect_result(
        self,
        *,
        session: _BashExecSession,
        yield_time_ms: int,
        chars_written: int,
    ) -> BashExecSessionResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (max(1, yield_time_ms) / 1000.0)
        post_exit_deadline: float | None = None
        stdout = bytearray()
        stderr = bytearray()
        stdout_truncated = False
        stderr_truncated = False

        while True:
            snapshot = await self._drain_session_buffers(session)
            stdout_append_truncated = self._append_bounded(stdout, snapshot.stdout)
            stderr_append_truncated = self._append_bounded(stderr, snapshot.stderr)
            stdout_truncated |= snapshot.stdout_truncated | stdout_append_truncated
            stderr_truncated |= snapshot.stderr_truncated | stderr_append_truncated

            streams_closed = snapshot.stdout_closed and snapshot.stderr_closed
            if snapshot.exit_code is not None and streams_closed:
                await self._discard_session(session.session_id, terminate=False)
                return self._build_result(
                    session=session,
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    exit_code=snapshot.exit_code,
                    session_id=None,
                    running=False,
                    chars_written=chars_written,
                )

            now = loop.time()
            if snapshot.exit_code is not None and post_exit_deadline is None:
                post_exit_deadline = now + _POST_EXIT_GRACE_SEC

            if snapshot.exit_code is None and now >= deadline:
                return self._build_result(
                    session=session,
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    exit_code=None,
                    session_id=session.session_id,
                    running=True,
                    chars_written=chars_written,
                )

            if snapshot.exit_code is not None and post_exit_deadline is not None and now >= post_exit_deadline:
                await self._discard_session(session.session_id, terminate=False)
                return self._build_result(
                    session=session,
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    exit_code=snapshot.exit_code,
                    session_id=None,
                    running=False,
                    chars_written=chars_written,
                )

            timeout_sec = (
                (post_exit_deadline - now)
                if snapshot.exit_code is not None and post_exit_deadline is not None
                else (deadline - now)
            )
            await self._wait_for_session_change(
                session=session,
                version=snapshot.version,
                timeout_sec=max(0.0, timeout_sec),
            )

    def _attach_background_tasks(self, session: _BashExecSession) -> None:
        session.tasks.extend(
            [
                asyncio.create_task(
                    self._pump_stream(
                        session=session,
                        stream=session.process.stdout,
                        stream_name="stdout",
                    )
                ),
                asyncio.create_task(
                    self._pump_stream(
                        session=session,
                        stream=session.process.stderr,
                        stream_name="stderr",
                    )
                ),
                asyncio.create_task(self._wait_for_exit(session)),
            ]
        )

    async def _pump_stream(
        self,
        *,
        session: _BashExecSession,
        stream: asyncio.StreamReader | None,
        stream_name: str,
    ) -> None:
        try:
            if stream is None:
                return
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                async with session.condition:
                    buffer = (
                        session.stdout_buffer if stream_name == "stdout" else session.stderr_buffer
                    )
                    truncated_attr = (
                        "stdout_truncated" if stream_name == "stdout" else "stderr_truncated"
                    )
                    if self._append_bounded(buffer, chunk):
                        setattr(session, truncated_attr, True)
                    session.version += 1
                    session.condition.notify_all()
        finally:
            async with session.condition:
                if stream_name == "stdout":
                    session.stdout_closed = True
                else:
                    session.stderr_closed = True
                session.version += 1
                session.condition.notify_all()

    async def _wait_for_exit(self, session: _BashExecSession) -> None:
        try:
            await session.process.wait()
        finally:
            async with session.condition:
                session.exit_code = int(session.process.returncode or 0)
                session.version += 1
                session.condition.notify_all()

    async def _drain_session_buffers(self, session: _BashExecSession) -> _DrainSnapshot:
        async with session.condition:
            stdout = bytes(session.stdout_buffer)
            stderr = bytes(session.stderr_buffer)
            stdout_truncated = session.stdout_truncated
            stderr_truncated = session.stderr_truncated
            session.stdout_buffer.clear()
            session.stderr_buffer.clear()
            session.stdout_truncated = False
            session.stderr_truncated = False
            return _DrainSnapshot(
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                stdout_closed=session.stdout_closed,
                stderr_closed=session.stderr_closed,
                exit_code=session.exit_code,
                version=session.version,
            )

    async def _wait_for_session_change(
        self,
        *,
        session: _BashExecSession,
        version: int,
        timeout_sec: float,
    ) -> None:
        if timeout_sec <= 0:
            return
        async with session.condition:
            if session.version != version:
                return
            try:
                await asyncio.wait_for(session.condition.wait(), timeout=timeout_sec)
            except TimeoutError:
                return

    async def _get_session(self, session_id: str) -> _BashExecSession:
        async with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise ValueError(f"Unknown session_id: {session_id}") from exc

    async def _discard_session(self, session_id: str, *, terminate: bool) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        if terminate:
            terminate_process_tree(session.process.pid, signal.SIGTERM)
        stdin = session.process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()
        for task in session.tasks:
            task.cancel()

    def _build_result(
        self,
        *,
        session: _BashExecSession,
        stdout: bytes,
        stderr: bytes,
        stdout_truncated: bool,
        stderr_truncated: bool,
        exit_code: int | None,
        session_id: str | None,
        running: bool,
        chars_written: int,
    ) -> BashExecSessionResult:
        return BashExecSessionResult(
            display_cmd=session.display_cmd,
            cwd_label=session.cwd_label,
            env_keys=session.env_keys,
            shell=session.shell,
            login_requested=session.login_requested,
            login_applied=session.login_applied,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            exit_code=exit_code,
            session_id=session_id,
            running=running,
            chars_written=chars_written,
            redacted_values=session.redacted_values,
        )

    def _append_bounded(self, target: bytearray, chunk: bytes) -> bool:
        if not chunk:
            return False
        remaining = self._max_buffer_bytes - len(target)
        truncated = len(chunk) > max(0, remaining)
        if remaining > 0:
            target.extend(chunk[:remaining])
        return truncated

    def _new_session_id(self) -> str:
        return f"bash-{uuid4().hex[:12]}"
