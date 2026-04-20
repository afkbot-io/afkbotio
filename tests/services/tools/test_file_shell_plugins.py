"""Tests for file.* and bash.exec plugins."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.credential_placeholders import redact_secret_fragments
from afkbot.services.tools.plugins.bash_exec.plugin import BashExecTool, _StreamingOutputTail
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


def _registry(settings: Settings) -> ToolRegistry:
    return ToolRegistry.from_plugins(
        (
            "session_job_run",
            "file_list",
            "file_read",
            "file_write",
            "file_edit",
            "file_search",
            "bash_exec",
        ),
        settings=settings,
    )


async def _set_allowed_directories(
    *,
    settings: Settings,
    profile_id: str,
    directories: list[Path],
    enabled: bool | None = None,
) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default(profile_id)
            policy = await ProfilePolicyRepository(session).get_or_create_default(profile_id)
            if enabled is not None:
                policy.policy_enabled = bool(enabled)
            policy.allowed_directories_json = json.dumps(
                [str(path.resolve(strict=False)) for path in directories],
                ensure_ascii=True,
            )
            await session.flush()
    finally:
        await engine.dispose()


async def test_file_tools_roundtrip(tmp_path: Path) -> None:
    """file.* tools should support write/read/edit/search/list workflow."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside-default.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    profile_root = tmp_path / "profiles/default"

    write_tool = registry.get("file.write")
    assert write_tool is not None
    write_params = write_tool.parse_params(
        {
            "profile_key": "default",
            "path": "tmp/demo.txt",
            "content": "hello world\nsecond line\n",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    write_result = await write_tool.execute(ctx, write_params)
    assert write_result.ok is True
    assert (profile_root / "tmp/demo.txt").read_text(encoding="utf-8").startswith("hello world")
    assert write_result.payload["after_text"] == "hello world\nsecond line\n"
    assert isinstance(write_result.payload.get("diff_suggestion"), dict)

    read_tool = registry.get("file.read")
    assert read_tool is not None
    read_params = read_tool.parse_params(
        {
            "profile_key": "default",
            "path": "tmp/demo.txt",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    read_result = await read_tool.execute(ctx, read_params)
    assert read_result.ok is True
    assert "hello world" in str(read_result.payload["content"])

    edit_tool = registry.get("file.edit")
    assert edit_tool is not None
    edit_params = edit_tool.parse_params(
        {
            "profile_key": "default",
            "path": "tmp/demo.txt",
            "search": "hello",
            "replace": "hi",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    edit_result = await edit_tool.execute(ctx, edit_params)
    assert edit_result.ok is True
    assert edit_result.payload["replacements"] == 1
    assert edit_result.payload["before_text"] == "hello world\nsecond line\n"
    assert edit_result.payload["after_text"] == "hi world\nsecond line\n"
    assert isinstance(edit_result.payload.get("diff_suggestion"), dict)

    search_tool = registry.get("file.search")
    assert search_tool is not None
    search_params = search_tool.parse_params(
        {
            "profile_key": "default",
            "path": "tmp",
            "query": "second",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    search_result = await search_tool.execute(ctx, search_params)
    assert search_result.ok is True
    assert search_result.payload["count"] == 1

    list_tool = registry.get("file.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "path": "tmp",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    assert any(item["path"] == "tmp/demo.txt" for item in list_result.payload["entries"])


async def test_file_read_applies_max_body_limit(tmp_path: Path) -> None:
    """file.read should truncate output by runtime max body bytes."""

    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "a.txt").write_text("0123456789abcdef", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, runtime_max_body_bytes=8)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("file.read")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": "a.txt",
            "max_bytes": 32,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["truncated"] is True
    assert len(str(result.payload["content"])) == 8


async def test_session_job_run_runs_non_interactive_commands_concurrently(tmp_path: Path) -> None:
    """session.job.run should run independent non-interactive commands concurrently."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("session.job.run")
    assert tool is not None

    python_bin = shlex.quote(sys.executable)
    params = tool.parse_params(
        {
            "jobs": [
                {
                    "kind": "bash",
                    "cmd": f"{python_bin} -c \"import time; time.sleep(0.25); print('first')\"",
                },
                {
                    "kind": "bash",
                    "cmd": f"{python_bin} -c \"import time; time.sleep(0.25); print('second')\"",
                },
            ],
            "timeout_sec": 2,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    started = time.monotonic()
    result = await tool.execute(ctx, params)
    elapsed = time.monotonic() - started

    assert result.ok is True
    assert result.payload["completed"] == 2
    assert elapsed < 0.65
    outputs = [
        str(item["payload"]["stdout"]).strip()
        for item in result.payload["results"]
        if isinstance(item["payload"], dict)
    ]
    assert outputs == ["first", "second"]


def test_session_job_run_outer_timeout_covers_nested_command_timeout(tmp_path: Path) -> None:
    """Nested per-command timeouts should extend the outer batch wrapper."""

    settings = Settings(root_dir=tmp_path, tool_timeout_default_sec=5, tool_timeout_max_sec=30)
    registry = _registry(settings)
    tool = registry.get("session.job.run")
    assert tool is not None

    params = tool.parse_params(
        {
            "jobs": [
                {"kind": "bash", "cmd": "echo first", "timeout_sec": 20},
                {"kind": "bash", "cmd": "echo second", "timeout_sec": 10},
            ],
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    assert params.timeout_sec == 20


async def test_session_job_run_suppresses_nested_progress_callback(tmp_path: Path) -> None:
    """session.job.run should not emit concurrent nested bash.exec progress events."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    progress_events: list[dict[str, object]] = []

    async def _capture_progress(payload: dict[str, object]) -> None:
        progress_events.append({str(key): value for key, value in payload.items()})

    ctx = ToolContext(
        profile_id="default",
        session_id="s",
        run_id=1,
        progress_callback=_capture_progress,
    )
    tool = registry.get("session.job.run")
    assert tool is not None
    params = tool.parse_params(
        {
            "jobs": [
                {"kind": "bash", "cmd": "printf 'one\\n'; sleep 0.2; printf 'two\\n'"},
                {"kind": "bash", "cmd": "printf 'three\\n'; sleep 0.2; printf 'four\\n'"},
            ],
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    result = await tool.execute(ctx, params)

    assert result.ok is True
    assert progress_events == []


async def test_session_job_run_terminates_all_process_groups_when_parent_task_is_cancelled(
    tmp_path: Path,
) -> None:
    """Parent cancellation should terminate every running session.job.run child command."""

    if not hasattr(os, "killpg"):
        pytest.skip("Process groups are not supported on this platform")

    pid_files = [tmp_path / "bash-batch-pgid-1.txt", tmp_path / "bash-batch-pgid-2.txt"]
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("session.job.run")
    assert tool is not None
    params = tool.parse_params(
        {
            "jobs": [
                {
                    "kind": "bash",
                    "cmd": f"printf '%s' $$ > {shlex.quote(str(pid_files[0]))}; sleep 30",
                },
                {
                    "kind": "bash",
                    "cmd": f"printf '%s' $$ > {shlex.quote(str(pid_files[1]))}; sleep 30",
                },
            ],
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    task = asyncio.create_task(tool.execute(ctx, params))
    pgids: list[int] = []
    for pid_file in pid_files:
        for _ in range(100):
            if pid_file.exists():
                raw_pgid = pid_file.read_text(encoding="utf-8").strip()
                if raw_pgid:
                    pgids.append(int(raw_pgid))
                    break
            await asyncio.sleep(0.01)
        else:
            pytest.fail(f"session.job.run did not write pgid file: {pid_file}")

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    for pgid in pgids:
        for _ in range(100):
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:  # pragma: no cover - deterministic assertion branch
            pytest.fail(f"session.job.run left process group running: {pgid}")


async def test_file_read_streams_without_read_bytes(tmp_path: Path, monkeypatch) -> None:
    """file.read should not rely on Path.read_bytes for bounded reads."""

    content = b"0123456789abcdef"
    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "a.txt").write_bytes(content)
    settings = Settings(root_dir=tmp_path, runtime_max_body_bytes=8)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("file.read")
    assert tool is not None

    def _forbidden_read_bytes(_: Path) -> bytes:
        raise AssertionError("Path.read_bytes must not be called by file.read")

    monkeypatch.setattr(Path, "read_bytes", _forbidden_read_bytes)
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": "a.txt",
            "max_bytes": 16,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["content"] == "01234567"
    assert result.payload["truncated"] is True
    assert result.payload["size_bytes"] == len(content)


async def test_bash_exec_runs_command(tmp_path: Path) -> None:
    """bash.exec should execute command in workspace and return stdout/stderr."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside-allowed.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf 'ok'",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["exit_code"] == 0
    assert result.payload["stdout"] == "ok"
    assert result.payload["cwd"] == "."


async def test_bash_exec_pwd_defaults_to_profile_workspace(tmp_path: Path) -> None:
    """bash.exec should treat the profile workspace as the default cwd."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'custom-scope.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "pwd",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert str(result.payload["stdout"]).strip() == str((tmp_path / "profiles/default").resolve())
    assert result.payload["cwd"] == "."


async def test_full_access_invocation_cwd_sets_relative_base_without_narrowing_scope(
    tmp_path: Path,
) -> None:
    """Full-access profiles should start in the invocation cwd without shrinking file scope."""

    invocation_cwd = tmp_path / "checkout"
    invocation_cwd.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'full-access.db'}",
        root_dir=tmp_path,
        tool_invocation_cwd=invocation_cwd,
    )
    await _set_allowed_directories(
        settings=settings,
        profile_id="default",
        directories=[Path("/")],
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    bash_tool = registry.get("bash.exec")
    assert bash_tool is not None
    bash_params = bash_tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "pwd",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    bash_result = await bash_tool.execute(ctx, bash_params)
    assert bash_result.ok is True
    assert str(bash_result.payload["stdout"]).strip() == str(invocation_cwd.resolve())
    assert bash_result.payload["cwd"] == "."

    read_tool = registry.get("file.read")
    assert read_tool is not None
    read_params = read_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    read_result = await read_tool.execute(ctx, read_params)
    assert read_result.ok is True
    assert read_result.payload["content"] == "outside"


async def test_policy_disabled_removes_hard_workspace_scope(tmp_path: Path) -> None:
    """Disabled policy should not keep file tools pinned to the profile workspace."""

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'policy-disabled.db'}",
        root_dir=tmp_path,
    )
    await _set_allowed_directories(
        settings=settings,
        profile_id="default",
        directories=[tmp_path / "profiles/default"],
        enabled=False,
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    read_tool = registry.get("file.read")
    assert read_tool is not None
    read_params = read_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    read_result = await read_tool.execute(ctx, read_params)
    assert read_result.ok is True
    assert read_result.payload["content"] == "outside"


async def test_bash_exec_clamps_timeout_above_runtime_max(tmp_path: Path) -> None:
    """bash.exec should normalize oversized model timeouts instead of failing validation."""

    # Arrange
    settings = Settings(root_dir=tmp_path, tool_timeout_max_sec=5)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf 'ok'",
            "timeout_sec": 600,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert params.timeout_sec == 5
    assert result.ok is True
    assert result.payload["stdout"] == "ok"


async def test_bash_exec_streams_output_without_process_communicate(
    tmp_path: Path, monkeypatch
) -> None:
    """bash.exec should stream output and avoid Process.communicate buffering."""

    settings = Settings(root_dir=tmp_path, runtime_max_body_bytes=64)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None

    async def _forbidden_communicate(_: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        raise AssertionError("Process.communicate must not be called by bash.exec")

    monkeypatch.setattr(asyncio.subprocess.Process, "communicate", _forbidden_communicate)
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "yes x | head -c 4096",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["stdout_truncated"] is True
    assert result.payload["stderr_truncated"] is False
    assert len(str(result.payload["stdout"])) == 64


async def test_bash_exec_keeps_truncated_session_output_in_result(tmp_path: Path) -> None:
    """Interactive session chunks should still surface bounded output after truncation."""

    # Arrange
    settings = Settings(root_dir=tmp_path, runtime_max_body_bytes=4)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    script = "import sys, time; sys.stdout.write('ABCDEFGH'); sys.stdout.flush(); time.sleep(0.5)"

    # Act
    result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "cmd": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                "yield_time_ms": 100,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    # Assert
    assert result.ok is True
    assert result.payload["running"] is True
    assert result.payload["stdout_truncated"] is True
    assert result.payload["stdout"] == "ABCD"
    session_id = str(result.payload["session_id"])

    cleanup_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "session_id": session_id,
                "yield_time_ms": 700,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert cleanup_result.ok is True
    assert cleanup_result.payload["running"] is False


def test_streaming_output_tail_redacts_reassembled_secret_after_chunk_boundary() -> None:
    """Snapshot-time redaction should hide secrets even when chunks split the secret value."""

    # Arrange
    tail = _StreamingOutputTail()
    tail.add_chunk(stream_name="stdout", text="token bash-sec")
    tail.add_chunk(stream_name="stdout", text="ret-42\n")

    # Act
    preview_lines = tail.snapshot_lines(
        redact_line=lambda value: redact_secret_fragments(
            source=value,
            secret_values={"bash-secret-42"},
        )
    )

    # Assert
    assert preview_lines == ("stdout | token [REDACTED]",)


def test_streaming_output_tail_keeps_latest_ten_lines() -> None:
    """Streaming preview tail should retain the latest ten formatted lines."""

    # Arrange
    tail = _StreamingOutputTail()
    for index in range(1, 13):
        tail.add_chunk(stream_name="stdout", text=f"line-{index:02d}\n")

    # Act
    preview_lines = tail.snapshot_lines()

    # Assert
    assert preview_lines == tuple(f"stdout | line-{index:02d}" for index in range(3, 13))


async def test_bash_exec_emits_live_progress_preview_lines(tmp_path: Path) -> None:
    """bash.exec should emit rolling preview lines through the tool progress callback."""

    # Arrange
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside-default.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    progress_events: list[dict[str, object]] = []

    async def _capture_progress(payload: dict[str, object]) -> None:
        progress_events.append({str(key): value for key, value in payload.items()})

    ctx = ToolContext(
        profile_id="default",
        session_id="s",
        run_id=1,
        progress_callback=_capture_progress,
    )
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": (
                "printf 'one\\n'; "
                "sleep 0.2; "
                "printf 'two\\n'; "
                "sleep 0.2; "
                "printf 'three\\n'; "
                "sleep 0.2; "
                "printf 'four\\n'; "
                "sleep 0.2; "
                "printf 'five\\n'"
            ),
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert result.ok is True
    assert len(progress_events) >= 2
    assert progress_events[-1]["stream"] == "mixed"
    assert progress_events[-1]["preview_lines"] == [
        "stdout | one",
        "stdout | two",
        "stdout | three",
        "stdout | four",
        "stdout | five",
    ]


async def test_bash_exec_dedupes_identical_live_preview_snapshots(tmp_path: Path) -> None:
    """bash.exec should not emit the same preview snapshot twice in a row."""

    # Arrange
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside-allowed.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    progress_events: list[dict[str, object]] = []

    async def _capture_progress(payload: dict[str, object]) -> None:
        progress_events.append({str(key): value for key, value in payload.items()})

    ctx = ToolContext(
        profile_id="default",
        session_id="s",
        run_id=1,
        progress_callback=_capture_progress,
    )
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": ("printf 'one\\n'; sleep 0.2; printf 'two\\n'; sleep 0.2; printf 'three\\n'"),
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)
    snapshots = [
        tuple(str(line) for line in list(event.get("preview_lines") or []))
        for event in progress_events
    ]

    # Assert
    assert result.ok is True
    assert snapshots
    assert all(
        current != previous for previous, current in zip(snapshots, snapshots[1:], strict=False)
    )


async def test_bash_exec_terminates_process_group_when_task_is_cancelled(tmp_path: Path) -> None:
    """Task cancellation should terminate the bash.exec process group."""

    if not hasattr(os, "killpg"):
        pytest.skip("Process groups are not supported on this platform")

    # Arrange
    pid_file = tmp_path / "bash-exec-pgid.txt"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'custom-scope.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": f"printf '%s' $$ > {shlex.quote(str(pid_file))}; sleep 30",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    task = asyncio.create_task(tool.execute(ctx, params))
    pgid: int | None = None
    for _ in range(100):
        if pid_file.exists():
            raw_pgid = pid_file.read_text(encoding="utf-8").strip()
            if raw_pgid:
                pgid = int(raw_pgid)
                break
        await asyncio.sleep(0.01)
    assert pgid is not None

    # Act
    task.cancel()

    # Assert
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:  # pragma: no cover - deterministic assertion branch
        pytest.fail("bash.exec left its process group running after cancellation")


async def test_bash_exec_can_resume_interactive_prompt(tmp_path: Path) -> None:
    """bash.exec should return a live session and accept follow-up stdin input."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    script = (
        "import sys; "
        "sys.stdout.write('Ok to proceed? (y) '); "
        "sys.stdout.flush(); "
        "answer = sys.stdin.readline().strip(); "
        "print('answer=' + answer)"
    )
    start_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "cmd": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                "yield_time_ms": 100,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert start_result.ok is True
    assert start_result.payload["running"] is True
    assert "Ok to proceed?" in str(start_result.payload["stdout"])
    session_id = str(start_result.payload["session_id"])

    # Act
    resume_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "session_id": session_id,
                "chars": "y\n",
                "yield_time_ms": 500,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    # Assert
    assert resume_result.ok is True
    assert resume_result.payload["running"] is False
    assert resume_result.payload["exit_code"] == 0
    assert "answer=y" in str(resume_result.payload["stdout"])
    assert resume_result.payload.get("session_id") is None
    assert resume_result.payload["chars_written"] == 2


async def test_bash_exec_empty_poll_collects_later_output(tmp_path: Path) -> None:
    """bash.exec should let the agent poll an existing session without sending stdin."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    script = (
        "import sys, time; print('first', flush=True); time.sleep(0.3); print('second', flush=True)"
    )
    start_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "cmd": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                "yield_time_ms": 100,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert start_result.ok is True
    assert start_result.payload["running"] is True
    assert "first" in str(start_result.payload["stdout"])
    session_id = str(start_result.payload["session_id"])

    # Act
    poll_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "session_id": session_id,
                "yield_time_ms": 700,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    # Assert
    assert poll_result.ok is True
    assert poll_result.payload["running"] is False
    assert poll_result.payload["exit_code"] == 0
    assert "second" in str(poll_result.payload["stdout"])
    assert poll_result.payload.get("session_id") is None


async def test_bash_exec_rejects_finished_session_id_after_exit(tmp_path: Path) -> None:
    """bash.exec should discard finished sessions and reject later reuse."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    script = (
        "import sys; "
        "sys.stdout.write('Continue? '); "
        "sys.stdout.flush(); "
        "sys.stdin.readline(); "
        "print('done')"
    )
    start_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "cmd": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                "yield_time_ms": 100,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    session_id = str(start_result.payload["session_id"])
    finished_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "session_id": session_id,
                "chars": "\n",
                "yield_time_ms": 500,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )
    assert finished_result.ok is True
    assert finished_result.payload["exit_code"] == 0

    # Act
    reused_result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "session_id": session_id,
                "yield_time_ms": 100,
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    # Assert
    assert reused_result.ok is False
    assert reused_result.error_code == "bash_exec_invalid"
    assert "Unknown session_id" in str(reused_result.reason)


async def test_bash_exec_applies_env_overrides(tmp_path: Path) -> None:
    """bash.exec should merge allowed env overrides into the child process environment."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf '%s' \"$AFKBOT_TEST_VALUE\"",
            "env": {"AFKBOT_TEST_VALUE": "visible"},
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert result.ok is True
    assert result.payload["stdout"] == "visible"
    assert result.payload["env_keys"] == ["AFKBOT_TEST_VALUE"]


async def test_bash_exec_does_not_inherit_host_secret_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bash.exec should not expose AFKBOT/OpenAI/GitHub secrets from daemon env."""

    monkeypatch.setenv("OPENAI_API_KEY", "host-openai-secret")
    monkeypatch.setenv("AFKBOT_LLM_API_KEY", "host-afk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "host-github-secret")
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    script = (
        "import json, os; "
        "keys = ('OPENAI_API_KEY', 'AFKBOT_LLM_API_KEY', 'GITHUB_TOKEN'); "
        "print(json.dumps({key: os.environ[key] for key in keys if key in os.environ}, sort_keys=True))"
    )
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    result = await tool.execute(ctx, params)

    assert result.ok is True
    assert result.payload["stdout"].strip() == "{}"
    assert "host-openai-secret" not in result.payload["stdout"]
    assert "host-afk-secret" not in result.payload["stdout"]
    assert "host-github-secret" not in result.payload["stdout"]
    assert result.payload["env_keys"] == []


async def test_bash_exec_rejects_protected_env_overrides(tmp_path: Path) -> None:
    """bash.exec should reject env overrides for protected loader and linker variables."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None

    # Act
    result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "cmd": "printf 'blocked'",
                "env": {"PATH": "/tmp/bin"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    # Assert
    assert result.ok is False
    assert result.error_code == "bash_exec_invalid"
    assert "PATH" in str(result.reason)


async def test_bash_exec_rejects_literal_secret_env_overrides(tmp_path: Path) -> None:
    """Secret-like env override names should use credential placeholders, not literals."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None

    result = await tool.execute(
        ctx,
        tool.parse_params(
            {
                "profile_key": "default",
                "cmd": "printf 'blocked'",
                "env": {"OPENAI_API_KEY": "literal-secret"},
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        ),
    )

    assert result.ok is False
    assert result.error_code == "bash_exec_invalid"
    assert "OPENAI_API_KEY" in str(result.reason)
    assert "credential placeholder" in str(result.reason)


async def test_bash_exec_respects_requested_shell_and_login_flag(tmp_path: Path) -> None:
    """bash.exec should resolve an explicit shell and report whether login mode was applied."""

    # Arrange
    requested_shell = shutil.which("bash") or shutil.which("zsh") or shutil.which("fish")
    if requested_shell is None:
        pytest.skip("No supported login shell available")

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf 'ok'",
            "shell": requested_shell,
            "login": True,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert result.ok is True
    assert Path(str(result.payload["shell"])).name == Path(requested_shell).name
    assert result.payload["login_requested"] is True
    assert result.payload["login_applied"] is True


async def test_bash_exec_rejects_unsupported_requested_shell(tmp_path: Path) -> None:
    """bash.exec should fail fast when the requested shell is not an allowed shell executable."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf 'ok'",
            "shell": sys.executable,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert result.ok is False
    assert result.error_code == "bash_exec_invalid"
    assert "Unsupported shell executable" in str(result.reason)


async def test_bash_exec_rejects_requested_shell_from_untrusted_directory(tmp_path: Path) -> None:
    """bash.exec should reject shell binaries that do not come from trusted local shell locations."""

    # Arrange
    fake_shell = tmp_path / "bash"
    fake_shell.write_text("#!/bin/sh\nprintf fake\n", encoding="utf-8")
    fake_shell.chmod(0o755)

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("bash.exec")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf 'ok'",
            "shell": str(fake_shell),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )

    # Act
    result = await tool.execute(ctx, params)

    # Assert
    assert result.ok is False
    assert result.error_code == "bash_exec_invalid"
    assert "Unsupported shell executable" in str(result.reason)


def test_bash_exec_resolve_shell_path_skips_fallback_lookups_when_requested_shell_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bash.exec should avoid fallback which() probes when the requested shell resolves immediately."""

    # Arrange
    requested_shell = shutil.which("bash") or shutil.which("sh")
    if requested_shell is None:
        pytest.skip("No supported shell available")

    def _unexpected_which(_: str) -> str:
        raise AssertionError("Fallback shell lookup should not run")

    monkeypatch.setattr(shutil, "which", _unexpected_which)

    # Act
    resolved_shell = BashExecTool._resolve_shell_path(requested_shell=requested_shell)

    # Assert
    assert Path(resolved_shell).name == Path(requested_shell).name


def test_bash_exec_resolve_shell_path_builds_trusted_directory_snapshot_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bash.exec should reuse one trusted-directory snapshot during shell path resolution."""

    # Arrange
    requested_shell = shutil.which("bash") or shutil.which("sh")
    if requested_shell is None:
        pytest.skip("No supported shell available")

    calls = 0
    original = BashExecTool._trusted_shell_directories

    def _counted() -> set[Path]:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(BashExecTool, "_trusted_shell_directories", staticmethod(_counted))

    # Act
    resolved_shell = BashExecTool._resolve_shell_path(requested_shell=requested_shell)

    # Assert
    assert Path(resolved_shell).name == Path(requested_shell).name
    assert calls == 1


def test_bash_exec_resolve_shell_path_accepts_busybox_style_shell_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bash.exec should preserve shell aliases such as sh -> busybox on Alpine-like systems."""

    # Arrange
    busybox_binary = tmp_path / "busybox"
    busybox_binary.write_text("#!/bin/sh\nprintf busybox\n", encoding="utf-8")
    busybox_binary.chmod(0o755)
    shell_alias = tmp_path / "sh"
    shell_alias.symlink_to(busybox_binary)
    monkeypatch.setattr(
        BashExecTool,
        "_trusted_shell_directories",
        staticmethod(lambda: {tmp_path}),
    )

    # Act
    resolved_shell = BashExecTool._resolve_shell_path(requested_shell=str(shell_alias))

    # Assert
    assert resolved_shell == str(shell_alias)


async def test_file_tools_reject_absolute_paths_outside_profile_workspace_by_default(
    tmp_path: Path,
) -> None:
    """Without explicit broader policy scope, tools stay inside the active profile workspace."""

    outside = tmp_path / "external.txt"
    outside.write_text("outside", encoding="utf-8")
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside-default.db'}",
        root_dir=tmp_path,
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("file.read")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is False
    assert result.error_code == "file_read_invalid"
    assert "outside scope" in str(result.reason).lower()


async def test_file_tools_allow_absolute_paths_from_profile_policy_scope(tmp_path: Path) -> None:
    """Profile policy allowed_directories should widen hard scope beyond the default profile root."""

    outside = tmp_path / "external.txt"
    outside.write_text("outside", encoding="utf-8")
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside-allowed.db'}",
        root_dir=tmp_path,
    )
    await _set_allowed_directories(
        settings=settings,
        profile_id="default",
        directories=[tmp_path / "profiles/default", outside.parent],
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("file.read")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["path"] == str(outside.resolve())
    assert result.payload["content"] == "outside"


async def test_custom_scope_keeps_profile_workspace_and_extra_roots(tmp_path: Path) -> None:
    """Custom scope should keep the profile workspace while allowing explicit extra roots."""

    outside_dir = tmp_path / "external"
    outside_dir.mkdir()
    outside = outside_dir / "external.txt"
    outside.write_text("outside", encoding="utf-8")
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'custom-scope.db'}",
        root_dir=tmp_path,
    )
    await _set_allowed_directories(
        settings=settings,
        profile_id="default",
        directories=[outside_dir],
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    read_tool = registry.get("file.read")
    assert read_tool is not None
    read_params = read_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    read_result = await read_tool.execute(ctx, read_params)
    assert read_result.ok is True
    assert read_result.payload["content"] == "outside"

    bash_tool = registry.get("bash.exec")
    assert bash_tool is not None
    bash_params = bash_tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "pwd",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    bash_result = await bash_tool.execute(ctx, bash_params)
    assert bash_result.ok is True
    assert str(bash_result.payload["stdout"]).strip() == str(
        (tmp_path / "profiles/default").resolve()
    )
    assert bash_result.payload["cwd"] == "."


async def test_file_tools_enforce_hard_workspace_override_scope(tmp_path: Path) -> None:
    """Explicit shared workspace override should remain a hard tool-level scope."""

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, tool_workspace_root=shared_root)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("file.read")
    assert tool is not None
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is False
    assert result.error_code == "file_read_invalid"
    assert "outside scope" in str(result.reason).lower()


async def test_file_and_bash_tools_validate_profile(tmp_path: Path) -> None:
    """file.* and bash.exec should reject mismatched profile ids."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    for tool_name, params in (
        ("file.list", {"path": "."}),
        ("bash.exec", {"cmd": "echo 1", "cwd": "."}),
    ):
        tool = registry.get(tool_name)
        assert tool is not None
        validated = tool.parse_params(
            {"profile_key": "other", **params},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await tool.execute(ctx, validated)
        assert result.ok is False
        assert result.error_code == "profile_not_found"


async def test_file_search_rejects_parent_glob(tmp_path: Path) -> None:
    """file.search must reject glob patterns with parent traversal."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("file.search")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": ".",
            "query": "x",
            "glob": "../*.txt",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is False
    assert result.error_code == "file_search_invalid"


async def test_file_search_streams_without_read_bytes(tmp_path: Path, monkeypatch) -> None:
    """file.search should read bounded prefixes without Path.read_bytes."""

    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "a.txt").write_text("needle\n", encoding="utf-8")
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("file.search")
    assert tool is not None

    def _forbidden_read_bytes(_: Path) -> bytes:
        raise AssertionError("Path.read_bytes must not be called by file.search")

    monkeypatch.setattr(Path, "read_bytes", _forbidden_read_bytes)
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": ".",
            "query": "needle",
            "glob": "*.txt",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["count"] == 1


async def test_file_list_stops_iteration_after_max_entries(tmp_path: Path, monkeypatch) -> None:
    """file.list should stop consuming recursive iterators once max_entries is reached."""

    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "a.txt").write_text("a", encoding="utf-8")
    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)
    tool = registry.get("file.list")
    assert tool is not None

    original_rglob = Path.rglob

    def _guarded_rglob(self: Path, pattern: str) -> object:
        if self != profile_root:
            return original_rglob(self, pattern)

        def _iter():
            yield profile_root / "a.txt"
            raise AssertionError("file.list consumed recursive iterator after reaching max_entries")

        return _iter()

    monkeypatch.setattr(Path, "rglob", _guarded_rglob)
    params = tool.parse_params(
        {
            "profile_key": "default",
            "path": ".",
            "recursive": True,
            "include_hidden": True,
            "max_entries": 1,
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is True
    assert len(result.payload["entries"]) == 1
    assert str(result.payload["entries"][0]["path"]).endswith("a.txt")


async def test_file_tools_reject_absolute_paths_outside_hard_workspace_override(
    tmp_path: Path,
) -> None:
    """Explicit hard workspace override should reject absolute paths outside that scope."""

    workspace = tmp_path / "workspace"
    outside_dir = tmp_path / "outside"
    outside_file = outside_dir / "demo.txt"
    workspace.mkdir(parents=True, exist_ok=True)
    outside_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(root_dir=workspace, tool_workspace_root=workspace)
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    write_tool = registry.get("file.write")
    assert write_tool is not None
    write_params = write_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside_file),
            "content": "hello outside\nneedle\n",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    write_result = await write_tool.execute(ctx, write_params)
    assert write_result.ok is False
    assert write_result.error_code == "file_write_invalid"
    assert "Path outside scope" in (write_result.reason or "")

    read_tool = registry.get("file.read")
    assert read_tool is not None
    read_params = read_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside_file),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    read_result = await read_tool.execute(ctx, read_params)
    assert read_result.ok is False
    assert read_result.error_code == "file_read_invalid"
    assert "Path outside scope" in (read_result.reason or "")

    edit_tool = registry.get("file.edit")
    assert edit_tool is not None
    edit_params = edit_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside_file),
            "search": "hello",
            "replace": "hi",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    edit_result = await edit_tool.execute(ctx, edit_params)
    assert edit_result.ok is False
    assert edit_result.error_code == "file_edit_invalid"
    assert "Path outside scope" in (edit_result.reason or "")

    search_tool = registry.get("file.search")
    assert search_tool is not None
    search_params = search_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside_dir),
            "query": "needle",
            "glob": "*.txt",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    search_result = await search_tool.execute(ctx, search_params)
    assert search_result.ok is False
    assert search_result.error_code == "file_search_invalid"
    assert "Path outside scope" in (search_result.reason or "")

    list_tool = registry.get("file.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(outside_dir),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is False
    assert list_result.error_code == "file_list_invalid"
    assert "Path outside scope" in (list_result.reason or "")


async def test_file_tools_support_absolute_paths_inside_root_dir(tmp_path: Path) -> None:
    """file.* tools should support broader absolute paths when profile policy explicitly allows them."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    in_scope_file = workspace / "demo.txt"
    in_scope_file.write_text("hello inside\nneedle\n", encoding="utf-8")

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{workspace / 'absolute-paths.db'}",
        root_dir=workspace,
    )
    await _set_allowed_directories(
        settings=settings,
        profile_id="default",
        directories=[workspace],
    )
    registry = _registry(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    read_tool = registry.get("file.read")
    assert read_tool is not None
    read_params = read_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(in_scope_file),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    read_result = await read_tool.execute(ctx, read_params)
    assert read_result.ok is True
    assert "hello inside" in str(read_result.payload["content"])

    edit_tool = registry.get("file.edit")
    assert edit_tool is not None
    edit_params = edit_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(in_scope_file),
            "search": "hello",
            "replace": "hi",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    edit_result = await edit_tool.execute(ctx, edit_params)
    assert edit_result.ok is True
    assert edit_result.payload["replacements"] == 1

    search_tool = registry.get("file.search")
    assert search_tool is not None
    search_params = search_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(workspace),
            "query": "needle",
            "glob": "*.txt",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    search_result = await search_tool.execute(ctx, search_params)
    assert search_result.ok is True
    assert search_result.payload["count"] == 1

    list_tool = registry.get("file.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "path": str(workspace),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    assert list_result.payload["base_path"] == str(workspace.resolve())
