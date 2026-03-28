"""Tool plugin for bash.exec."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import os
from os import PathLike
import signal
import stat
from pathlib import Path
import shutil
import time
from typing import Any

from pydantic import Field, model_validator

from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.tools.base import ToolBase, ToolContext, ToolProgressCallback, ToolResult
from afkbot.services.tools.credential_placeholders import (
    redact_secret_fragments,
    resolve_secret_placeholders,
)
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters, build_tool_parameters
from afkbot.services.tools.plugins.bash_exec.runtime import (
    BashExecSessionManager,
    BashExecSessionResult,
    BashExecSessionStartRequest,
    terminate_process_tree,
)
from afkbot.services.tools.workspace import (
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    resolve_workspace_path,
    to_workspace_relative,
)
from afkbot.settings import Settings

_DEFAULT_RESUME_YIELD_TIME_MS = 1000
_PROTECTED_ENV_NAMES = {"PATH"}
_PROTECTED_ENV_PREFIXES = ("LD_", "DYLD_")
_ALLOWED_SHELL_NAMES = {"ash", "bash", "dash", "fish", "ksh", "sh", "zsh"}
_LOGIN_SHELL_NAMES = {"bash", "fish", "zsh"}


@dataclass(frozen=True)
class _ResolvedExecutablePath:
    """Executable candidate preserving both alias and resolved target paths."""

    candidate_path: Path
    resolved_path: Path


class _StreamingOutputTail:
    """Track a bounded mixed stdout/stderr preview for live tool progress updates."""

    def __init__(self, *, max_lines: int = 10, max_line_chars: int = 200) -> None:
        self._max_lines = max(1, int(max_lines))
        self._max_line_chars = max(16, int(max_line_chars))
        self._lines: deque[tuple[str, str]] = deque(maxlen=self._max_lines)
        self._partials: dict[str, str] = {"stdout": "", "stderr": ""}

    def add_chunk(self, *, stream_name: str, text: str) -> None:
        """Ingest one decoded chunk and update the mixed rolling preview."""

        normalized_stream = stream_name if stream_name in {"stdout", "stderr"} else "stdout"
        normalized_text = text.replace("\r", "\n")
        combined = self._partials[normalized_stream] + normalized_text
        parts = combined.split("\n")
        self._partials[normalized_stream] = parts.pop() if parts else ""
        for line in parts:
            self._append_line(stream_name=normalized_stream, line=line)

    def snapshot_lines(
        self,
        *,
        redact_line: Callable[[str], str] | None = None,
    ) -> tuple[str, ...]:
        """Return the latest bounded preview lines, including current partial lines."""

        lines = [
            self._format_line(
                stream_name=stream_name,
                line=line,
                redact_line=redact_line,
            )
            for stream_name, line in self._lines
        ]
        for stream_name in ("stdout", "stderr"):
            partial = self._partials[stream_name].strip()
            if partial:
                lines.append(
                    self._format_line(
                        stream_name=stream_name,
                        line=partial,
                        redact_line=redact_line,
                    )
                )
        return tuple(lines[-self._max_lines :])

    def _append_line(self, *, stream_name: str, line: str) -> None:
        cleaned = line.strip()
        if not cleaned:
            return
        self._lines.append((stream_name, cleaned))

    def _format_line(
        self,
        *,
        stream_name: str,
        line: str,
        redact_line: Callable[[str], str] | None = None,
    ) -> str:
        label = "stderr" if stream_name == "stderr" else "stdout"
        rendered_line = redact_line(line) if redact_line is not None else line
        shortened = (
            rendered_line
            if len(rendered_line) <= self._max_line_chars
            else f"{rendered_line[: self._max_line_chars - 3]}..."
        )
        return f"{label} | {shortened}"


class BashExecParams(RoutedToolParameters):
    """Parameters for `bash.exec`, including resumable interactive session calls."""

    cmd: str | None = Field(
        default=None,
        min_length=1,
        max_length=8000,
        description="Shell command for a new process. Omit when resuming an existing session.",
    )
    cwd: str = Field(default=".", min_length=1, max_length=4096)
    env: dict[str, str] = Field(default_factory=dict)
    shell: str | None = Field(default=None, min_length=1, max_length=256)
    login: bool = False
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Resume an active bash.exec session returned by a previous call.",
    )
    chars: str = Field(
        default="",
        max_length=8000,
        description="Characters to send to stdin for an existing session. Use empty string to poll.",
    )
    yield_time_ms: int | None = Field(
        default=None,
        ge=1,
        le=30000,
        description=(
            "When starting a command, return after this many milliseconds with partial output and "
            "a session_id if the process is still running. When resuming, defaults to 1000 ms."
        ),
    )

    @model_validator(mode="after")
    def _validate_mode(self) -> BashExecParams:
        session_id = str(self.session_id or "").strip()
        cmd = str(self.cmd or "").strip()

        if session_id and cmd:
            raise ValueError("cmd is not allowed when session_id is provided")
        if not session_id and not cmd:
            raise ValueError("cmd is required when session_id is not provided")
        if not session_id and self.chars:
            raise ValueError("chars is only allowed when resuming an existing session")

        self.session_id = session_id or None
        self.cmd = cmd or None
        return self


class BashExecTool(ToolBase):
    """Execute one shell command inside workspace scope."""

    name = "bash.exec"
    description = (
        "Execute one shell command with bounded output. Use for diagnostics, package management, "
        "service control, and other workspace-scoped shell tasks. For commands that may keep "
        "running or prompt for input, set `yield_time_ms`; if the result returns `session_id`, "
        "call `bash.exec` again with that `session_id` and optional `chars` until the process exits."
    )
    parameters_model = BashExecParams
    required_skill = "bash-exec"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session_manager = BashExecSessionManager(
            max_buffer_bytes=settings.runtime_max_body_bytes,
        )

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> BashExecParams:
        """Clamp model-generated timeout values to the runtime shell ceiling."""

        payload = dict(raw_params)
        raw_timeout_sec = payload.get("timeout_sec")
        try:
            normalized_timeout_sec = int(raw_timeout_sec) if raw_timeout_sec is not None else None
        except (TypeError, ValueError):
            normalized_timeout_sec = None
        if normalized_timeout_sec is not None and normalized_timeout_sec > max_timeout_sec:
            payload["timeout_sec"] = max_timeout_sec
        return build_tool_parameters(
            BashExecParams,
            payload,
            default_timeout_sec=default_timeout_sec,
            max_timeout_sec=max_timeout_sec,
        )

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=BashExecParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        try:
            if payload.session_id is not None:
                session_result = await self._session_manager.resume_session(
                    session_id=payload.session_id,
                    chars=payload.chars,
                    yield_time_ms=payload.yield_time_ms or _DEFAULT_RESUME_YIELD_TIME_MS,
                )
                return ToolResult(ok=True, payload=self._build_session_payload(session_result))

            base_dir = resolve_tool_workspace_base_dir(
                settings=self._settings,
                profile_id=ctx.profile_id,
            )
            scope_roots = await resolve_tool_workspace_scope_roots(
                settings=self._settings,
                profile_id=ctx.profile_id,
            )
            cwd = resolve_workspace_path(
                base_dir=base_dir,
                scope_roots=scope_roots,
                raw_path=payload.cwd,
                must_exist=True,
            )
            if not cwd.is_dir():
                raise ValueError(f"cwd is not a directory: {payload.cwd}")

            resolved_values: set[str] = set()
            assert payload.cmd is not None
            resolved_cmd = await resolve_secret_placeholders(
                settings=self._settings,
                profile_id=ctx.profile_id,
                source=payload.cmd,
                default_app_name="global",
                default_profile_name="default",
                tool_name=self.name,
                allowed_app_names={"global"},
                resolved_values=resolved_values,
            )
            resolved_env = await self._resolve_env_overrides(
                profile_id=ctx.profile_id,
                env=payload.env,
                resolved_values=resolved_values,
            )
            if payload.yield_time_ms is not None:
                session_result = await self._start_interactive_command(
                    resolved_cmd=resolved_cmd,
                    display_cmd=payload.cmd,
                    cwd=cwd,
                    env=resolved_env,
                    requested_shell=payload.shell,
                    login=payload.login,
                    yield_time_ms=payload.yield_time_ms,
                    cwd_label=to_workspace_relative(base_dir=base_dir, path=cwd),
                    redacted_values=resolved_values,
                )
                return ToolResult(ok=True, payload=self._build_session_payload(session_result))

            command_result = await self._run_command(
                cmd=resolved_cmd,
                display_cmd=payload.cmd,
                cwd=cwd,
                timeout_sec=payload.timeout_sec,
                env=resolved_env,
                requested_shell=payload.shell,
                login=payload.login,
                progress_callback=ctx.progress_callback,
                secret_values=resolved_values,
            )
            command_result["cwd"] = to_workspace_relative(base_dir=base_dir, path=cwd)
            if resolved_values:
                command_result["stdout"] = redact_secret_fragments(
                    source=str(command_result.get("stdout") or ""),
                    secret_values=resolved_values,
                )
                command_result["stderr"] = redact_secret_fragments(
                    source=str(command_result.get("stderr") or ""),
                    secret_values=resolved_values,
                )
            return ToolResult(ok=True, payload=command_result)
        except CredentialsServiceError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata={str(key): value for key, value in exc.details.items()},
            )
        except ValueError as exc:
            return ToolResult.error(error_code="bash_exec_invalid", reason=str(exc))
        except TimeoutError:
            return ToolResult.error(
                error_code="bash_exec_failed",
                reason=f"Command timed out after {payload.timeout_sec} seconds",
            )
        except OSError as exc:
            return ToolResult.error(error_code="bash_exec_failed", reason=f"{exc.__class__.__name__}: {exc}")

    async def _resolve_env_overrides(
        self,
        *,
        profile_id: str,
        env: Mapping[str, str],
        resolved_values: set[str],
    ) -> dict[str, str]:
        """Resolve credential placeholders inside environment overrides."""

        resolved: dict[str, str] = {}
        for raw_key, raw_value in env.items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("Environment variable names must be non-empty")
            normalized_key = key.upper()
            if normalized_key in _PROTECTED_ENV_NAMES or normalized_key.startswith(
                _PROTECTED_ENV_PREFIXES
            ):
                raise ValueError(f"Environment override is not allowed for {key}")
            value = await resolve_secret_placeholders(
                settings=self._settings,
                profile_id=profile_id,
                source=str(raw_value),
                default_app_name="global",
                default_profile_name="default",
                tool_name=self.name,
                allowed_app_names={"global"},
                resolved_values=resolved_values,
            )
            resolved[key] = value
        return resolved

    async def _start_interactive_command(
        self,
        *,
        resolved_cmd: str,
        display_cmd: str,
        cwd: Path,
        env: Mapping[str, str],
        requested_shell: str | None,
        login: bool,
        yield_time_ms: int,
        cwd_label: str,
        redacted_values: set[str],
    ) -> BashExecSessionResult:
        shell_path = self._resolve_shell_path(requested_shell=requested_shell)
        shell_args, login_applied = self._resolve_shell_args(
            shell_path=shell_path,
            login=login,
            command=resolved_cmd,
        )
        return await self._session_manager.start_session(
            request=BashExecSessionStartRequest(
                argv=(shell_path, *shell_args),
                cwd=cwd,
                env=self._build_process_env(env),
                display_cmd=display_cmd,
                cwd_label=cwd_label,
                env_keys=tuple(sorted(env.keys())),
                shell=shell_path,
                login_requested=login,
                login_applied=login_applied,
                redacted_values=frozenset(redacted_values),
            ),
            yield_time_ms=yield_time_ms,
        )

    @staticmethod
    def _build_session_payload(result: BashExecSessionResult) -> dict[str, object]:
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.redacted_values:
            stdout = redact_secret_fragments(
                source=stdout,
                secret_values=set(result.redacted_values),
            )
            stderr = redact_secret_fragments(
                source=stderr,
                secret_values=set(result.redacted_values),
            )
        payload: dict[str, object] = {
            "cmd": result.display_cmd,
            "cwd": result.cwd_label,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "shell": result.shell,
            "login_requested": result.login_requested,
            "login_applied": result.login_applied,
            "env_keys": list(result.env_keys),
            "running": result.running,
        }
        if result.exit_code is not None:
            payload["exit_code"] = result.exit_code
        if result.session_id is not None:
            payload["session_id"] = result.session_id
        if result.chars_written > 0:
            payload["chars_written"] = result.chars_written
        return payload

    async def _run_command(
        self,
        *,
        cmd: str,
        display_cmd: str,
        cwd: Path,
        timeout_sec: int,
        env: Mapping[str, str],
        requested_shell: str | None,
        login: bool,
        progress_callback: ToolProgressCallback | None = None,
        secret_values: set[str] | None = None,
    ) -> dict[str, object]:
        shell_path = self._resolve_shell_path(requested_shell=requested_shell)
        shell_args, login_applied = self._resolve_shell_args(
            shell_path=shell_path,
            login=login,
            command=cmd,
        )
        output_tail = _StreamingOutputTail()
        emit_lock = asyncio.Lock()
        last_emitted_at = 0.0
        last_preview_lines: tuple[str, ...] = ()
        resolved_secret_values = secret_values or set()

        def _redact_preview_line(line: str) -> str:
            if not resolved_secret_values:
                return line
            return redact_secret_fragments(
                source=line,
                secret_values=resolved_secret_values,
            )

        async def _emit_preview_chunk(stream_name: str, text: str) -> None:
            nonlocal last_emitted_at, last_preview_lines
            if progress_callback is None:
                return
            async with emit_lock:
                output_tail.add_chunk(stream_name=stream_name, text=text)
                now = time.monotonic()
                if now - last_emitted_at < 0.15:
                    return
                preview_lines = output_tail.snapshot_lines(redact_line=_redact_preview_line)
                if not preview_lines:
                    return
                if preview_lines == last_preview_lines:
                    return
                last_emitted_at = now
                last_preview_lines = preview_lines
                await progress_callback(
                    {
                        "preview_lines": list(preview_lines),
                        "stream": "mixed",
                    }
                )

        async def _flush_preview() -> None:
            nonlocal last_emitted_at, last_preview_lines
            if progress_callback is None:
                return
            async with emit_lock:
                preview_lines = output_tail.snapshot_lines(redact_line=_redact_preview_line)
                if not preview_lines:
                    return
                if preview_lines == last_preview_lines:
                    return
                last_emitted_at = time.monotonic()
                last_preview_lines = preview_lines
                await progress_callback(
                    {
                        "preview_lines": list(preview_lines),
                        "stream": "mixed",
                    }
                )

        process = await asyncio.create_subprocess_exec(
            shell_path,
            *shell_args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=self._build_process_env(env),
        )
        max_bytes = max(1, self._settings.runtime_max_body_bytes)
        stdout_reader = asyncio.create_task(
            self._read_stream_bounded(
                process.stdout,
                max_bytes=max_bytes,
                on_chunk=lambda text: _emit_preview_chunk("stdout", text),
            )
        )
        stderr_reader = asyncio.create_task(
            self._read_stream_bounded(
                process.stderr,
                max_bytes=max_bytes,
                on_chunk=lambda text: _emit_preview_chunk("stderr", text),
            )
        )

        try:
            await asyncio.wait_for(process.wait(), timeout=float(timeout_sec))
        except asyncio.CancelledError:
            await self._terminate_running_process(process, stdout_reader, stderr_reader)
            raise
        except TimeoutError:
            await self._terminate_running_process(process, stdout_reader, stderr_reader)
            raise

        stdout_raw, stdout_truncated = await stdout_reader
        stderr_raw, stderr_truncated = await stderr_reader
        await _flush_preview()
        return {
            "cmd": display_cmd,
            "exit_code": int(process.returncode if process.returncode is not None else 0),
            "stdout": stdout_raw.decode("utf-8", errors="replace"),
            "stderr": stderr_raw.decode("utf-8", errors="replace"),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "shell": shell_path,
            "login_requested": login,
            "login_applied": login_applied,
            "env_keys": sorted(env.keys()),
            "running": False,
        }

    async def _terminate_running_process(
        self,
        process: asyncio.subprocess.Process,
        stdout_reader: asyncio.Task[tuple[bytes, bool]],
        stderr_reader: asyncio.Task[tuple[bytes, bool]],
    ) -> None:
        """Terminate the active process group and drain stream readers."""

        if process.returncode is None:
            terminate_process_tree(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            if process.returncode is None:
                terminate_process_tree(process.pid, signal.SIGKILL)
                await process.wait()
        await asyncio.gather(stdout_reader, stderr_reader, return_exceptions=True)

    @staticmethod
    def _build_process_env(overrides: Mapping[str, str]) -> dict[str, str]:
        """Return process environment with user-provided overrides applied."""

        base_env = dict(os.environ)
        for key, value in overrides.items():
            base_env[str(key)] = str(value)
        return base_env

    @staticmethod
    def _resolve_shell_args(
        *,
        shell_path: str,
        login: bool,
        command: str,
    ) -> tuple[list[str], bool]:
        """Return shell invocation args and whether login mode was actually applied."""

        shell_name = Path(shell_path).name.lower()
        login_supported = shell_name in _LOGIN_SHELL_NAMES
        if login and login_supported:
            return ["-l", "-c", command], True
        return ["-c", command], False

    @staticmethod
    def _resolve_shell_path(*, requested_shell: str | None) -> str:
        """Resolve requested shell path or choose a deterministic local fallback."""

        trusted_directories = BashExecTool._trusted_shell_directories()
        normalized_requested_shell = str(requested_shell or "").strip()
        if normalized_requested_shell:
            resolved_requested_shell = BashExecTool._normalize_shell_candidate(
                normalized_requested_shell,
                trusted_directories=trusted_directories,
            )
            if resolved_requested_shell is None:
                raise ValueError(f"Unsupported shell executable: {normalized_requested_shell}")
            return resolved_requested_shell

        for raw_candidate in (os.environ.get("SHELL"), "bash", "sh"):
            resolved = BashExecTool._normalize_shell_candidate(
                raw_candidate,
                trusted_directories=trusted_directories,
            )
            if resolved is not None:
                return resolved
        raise ValueError("No usable shell executable found for bash.exec")

    @staticmethod
    def _normalize_shell_candidate(
        raw_candidate: str | PathLike[str] | None,
        *,
        trusted_directories: set[Path],
    ) -> str | None:
        candidate = str(raw_candidate or "").strip()
        if not candidate:
            return None
        resolved_executable = BashExecTool._resolve_existing_executable_path(candidate)
        if resolved_executable is None:
            return None
        return (
            str(resolved_executable.candidate_path)
            if BashExecTool._is_allowed_shell_path(
                resolved_executable,
                trusted_directories=trusted_directories,
            )
            else None
        )

    @staticmethod
    def _is_allowed_shell_path(
        executable: _ResolvedExecutablePath,
        *,
        trusted_directories: set[Path],
    ) -> bool:
        """Return whether an executable candidate points to a supported trusted shell."""

        shell_name = executable.candidate_path.name.lower()
        if shell_name not in _ALLOWED_SHELL_NAMES:
            return False
        try:
            if not executable.resolved_path.exists() or not executable.resolved_path.is_file():
                return False
            if not os.access(executable.resolved_path, os.X_OK):
                return False
        except OSError:
            return False
        return BashExecTool._is_trusted_shell_path(
            executable.candidate_path,
            trusted_directories=trusted_directories,
        )

    @staticmethod
    def _is_trusted_shell_path(
        path: Path,
        *,
        trusted_directories: set[Path],
    ) -> bool:
        """Return whether the resolved shell path comes from a trusted local location."""

        if path.parent not in trusted_directories:
            return False
        try:
            return (path.parent.stat().st_mode & stat.S_IWOTH) == 0
        except OSError:
            return False

    @staticmethod
    def _trusted_shell_directories() -> set[Path]:
        """Resolve trusted shell directories from the current local environment."""

        resolved_directories: set[Path] = set()
        resolved_env_shell = BashExecTool._resolve_existing_executable_path(os.environ.get("SHELL"))
        if resolved_env_shell is not None:
            resolved_directories.add(resolved_env_shell.candidate_path.parent)
            resolved_directories.add(resolved_env_shell.resolved_path.parent)
        for raw_directory in str(os.environ.get("PATH") or "").split(os.pathsep):
            candidate = raw_directory.strip()
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if not path.is_absolute():
                path = path.resolve(strict=False)
            resolved_path = path.resolve(strict=False)
            try:
                if path.exists() and path.is_dir():
                    resolved_directories.add(path)
                if resolved_path.exists() and resolved_path.is_dir():
                    resolved_directories.add(resolved_path)
            except OSError:
                continue
        return resolved_directories

    @staticmethod
    def _resolve_existing_executable_path(
        raw_candidate: str | PathLike[str] | None,
    ) -> _ResolvedExecutablePath | None:
        """Resolve a candidate to an existing executable while preserving shell aliases."""

        candidate = str(raw_candidate or "").strip()
        if not candidate:
            return None
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            located = shutil.which(candidate)
            if located is None:
                return None
            path = Path(located)
        if not path.is_absolute():
            path = path.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        try:
            if not resolved_path.exists() or not resolved_path.is_file() or not os.access(
                resolved_path, os.X_OK
            ):
                return None
        except OSError:
            return None
        return _ResolvedExecutablePath(candidate_path=path, resolved_path=resolved_path)

    @staticmethod
    async def _read_stream_bounded(
        stream: asyncio.StreamReader | None,
        *,
        max_bytes: int,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False

        data = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            if on_chunk is not None:
                await on_chunk(chunk.decode("utf-8", errors="replace"))
            remaining = max_bytes - len(data)
            if remaining > 0:
                data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        return bytes(data), truncated

def create_tool(settings: Settings) -> ToolBase:
    """Create bash.exec tool instance."""

    return BashExecTool(settings=settings)
