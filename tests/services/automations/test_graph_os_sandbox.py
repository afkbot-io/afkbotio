"""OS-level sandbox coverage for graph code nodes."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from afkbot.services.automations.graph.os_sandbox import (
    OSSandboxUnavailableError,
    build_code_node_launch,
    sandbox_exec_available,
)
from afkbot.settings import Settings


@pytest.mark.skipif(
    not sandbox_exec_available() or sys.platform != "darwin",
    reason="sandbox-exec integration is only available on macOS test hosts",
)
async def test_macos_os_sandbox_denies_external_file_reads(tmp_path: Path) -> None:
    """OS sandbox should block reads outside the worker tempdir/runtime roots."""

    secret_path = tmp_path / "host-secret.txt"
    secret_path.write_text("top-secret", encoding="utf-8")
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="auto",
    )

    launch = build_code_node_launch(
        base_argv=(sys.executable, "-c", f"from pathlib import Path; print(Path({str(secret_path)!r}).read_text())"),
        sandbox_root=worker_dir,
        settings=settings,
    )
    process = await asyncio.create_subprocess_exec(
        *launch.argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=worker_dir,
    )
    stdout, stderr = await process.communicate()

    assert process.returncode != 0
    assert "Operation not permitted" in stderr.decode("utf-8", errors="replace")
    assert "top-secret" not in stdout.decode("utf-8", errors="replace")


@pytest.mark.skipif(
    not sandbox_exec_available() or sys.platform != "darwin",
    reason="sandbox-exec integration is only available on macOS test hosts",
)
async def test_macos_os_sandbox_allows_tempdir_reads_and_writes(tmp_path: Path) -> None:
    """OS sandbox should still allow local tempdir IO used by code nodes."""

    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="auto",
    )

    launch = build_code_node_launch(
        base_argv=(
            sys.executable,
            "-c",
            (
                "from pathlib import Path\n"
                "path = Path('scratch.txt')\n"
                "path.write_text('ok', encoding='utf-8')\n"
                "print(path.read_text(encoding='utf-8'))\n"
            ),
        ),
        sandbox_root=worker_dir,
        settings=settings,
    )
    process = await asyncio.create_subprocess_exec(
        *launch.argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=worker_dir,
    )
    stdout, stderr = await process.communicate()

    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout.decode("utf-8", errors="replace").strip() == "ok"


@pytest.mark.skipif(
    not sandbox_exec_available() or sys.platform != "darwin",
    reason="sandbox-exec integration is only available on macOS test hosts",
)
async def test_macos_os_sandbox_denies_child_exec(tmp_path: Path) -> None:
    """OS sandbox should not permit the sandboxed worker to exec arbitrary binaries."""

    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="auto",
    )

    launch = build_code_node_launch(
        base_argv=(
            sys.executable,
            "-c",
            (
                "import os\n"
                "os.execv('/bin/echo', ['/bin/echo', 'sandbox-bypass'])\n"
            ),
        ),
        sandbox_root=worker_dir,
        settings=settings,
    )
    process = await asyncio.create_subprocess_exec(
        *launch.argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=worker_dir,
    )
    stdout, stderr = await process.communicate()

    assert process.returncode != 0
    assert "sandbox-bypass" not in stdout.decode("utf-8", errors="replace")
    assert "Operation not permitted" in stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize("sandbox_mode", ["auto", "required"])
def test_os_sandbox_enabled_modes_fail_closed_when_host_support_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sandbox_mode: str,
) -> None:
    """Enabled modes should reject hosts where no supported OS sandbox exists."""

    from afkbot.services.automations.graph import os_sandbox as os_sandbox_module

    monkeypatch.setattr(os_sandbox_module, "sandbox_exec_available", lambda: False)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox=sandbox_mode,
    )

    with pytest.raises(OSSandboxUnavailableError):
        build_code_node_launch(
            base_argv=(sys.executable, "-c", "print('ok')"),
            sandbox_root=tmp_path,
            settings=settings,
        )
