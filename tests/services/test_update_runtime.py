"""Tests for managed runtime update service."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from afkbot.services.managed_install import (
    MANAGED_APP_DIR_ENV,
    MANAGED_INSTALL_DIR_ENV,
    MANAGED_METADATA_PATH_ENV,
    MANAGED_RUNTIME_DIR_ENV,
    MANAGED_SOURCE_REF_ENV,
    MANAGED_SOURCE_URL_ENV,
)
from afkbot.services.setup.runtime_store import write_runtime_config
from afkbot.services.update_runtime import _wait_for_local_health
from afkbot.services.update_runtime import UpdateRuntimeError, run_update
from afkbot.settings import get_settings


def _prepare_settings(tmp_path: Path, monkeypatch: MonkeyPatch) -> object:
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    return get_settings()


def test_run_update_fast_forwards_checkout_and_restarts_service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Local update should fast-forward the checkout and run maintenance commands."""

    # Arrange
    settings = _prepare_settings(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8")
    commands: list[list[str]] = []
    afk_calls: list[tuple[str, ...]] = []
    rev_parse_values = iter(["before-sha", "before-sha", "after-sha"])

    def _fake_run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        commands.append(command)
        if command[:4] == ["git", "-C", str(tmp_path), "remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--cached", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{next(rev_parse_values)}\n", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="after-sha\n", stderr="")
        if command[:7] == ["git", "-C", str(tmp_path), "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_subcommand",
        lambda *, settings, args: afk_calls.append(args),  # type: ignore[no-untyped-call]
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service",
        lambda: True,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._wait_for_local_health",
        lambda *, settings, timeout_sec=90.0: True,
    )

    # Act
    result = run_update(settings)

    # Assert
    assert result.install_mode == "host"
    assert result.source_updated is True
    assert result.runtime_restarted is True
    assert afk_calls == [
        ("doctor", "--no-integrations", "--no-upgrades"),
        ("upgrade", "apply", "--quiet"),
    ]
    assert ["git", "-C", str(tmp_path), "fetch", "--depth", "1", "--no-tags", "origin", "main"] in commands
    assert ["git", "-C", str(tmp_path), "merge", "--ff-only", "FETCH_HEAD"] in commands
    assert [sys.executable, "-m", "pip", "install", "-e", str(tmp_path)] in commands


def test_run_update_resets_checkout_after_history_rewrite(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Local update should hard-reset when remote history was rewritten."""

    # Arrange
    settings = _prepare_settings(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8")
    commands: list[list[str]] = []
    afk_calls: list[tuple[str, ...]] = []
    rev_parse_values = iter(["before-sha", "before-sha", "after-sha"])

    def _fake_run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        commands.append(command)
        if command[:4] == ["git", "-C", str(tmp_path), "remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--cached", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{next(rev_parse_values)}\n", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="rewritten-sha\n", stderr="")
        if command[:7] == ["git", "-C", str(tmp_path), "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_subcommand",
        lambda *, settings, args: afk_calls.append(args),  # type: ignore[no-untyped-call]
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service",
        lambda: True,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._wait_for_local_health",
        lambda *, settings, timeout_sec=90.0: True,
    )

    # Act
    result = run_update(settings)

    # Assert
    assert result.install_mode == "host"
    assert result.source_updated is True
    assert result.runtime_restarted is True
    assert "Git source reset to origin/main after history rewrite" in result.details
    assert ("doctor", "--no-integrations", "--no-upgrades") in afk_calls
    assert ("upgrade", "apply", "--quiet") in afk_calls
    assert ["git", "-C", str(tmp_path), "reset", "--hard", "FETCH_HEAD"] in commands
    assert ["git", "-C", str(tmp_path), "merge", "--ff-only", "FETCH_HEAD"] not in commands


def test_run_update_rejects_dirty_checkout(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Local update should fail closed on a dirty worktree."""

    # Arrange
    settings = _prepare_settings(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8")

    def _fake_run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        if command[:4] == ["git", "-C", str(tmp_path), "remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path)

    # Act / Assert
    with pytest.raises(UpdateRuntimeError) as exc:
        run_update(settings)

    assert "clean git worktree" in exc.value.reason


def test_run_update_reinstalls_managed_snapshot_without_git(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Managed update should swap to a fresh source snapshot without requiring git."""

    # Arrange
    runtime_root = tmp_path / "runtime"
    install_dir = tmp_path / "managed"
    current_app_dir = install_dir / "app" / "current"
    current_app_dir.mkdir(parents=True, exist_ok=True)
    staged_source = tmp_path / "staged" / "source"
    (staged_source / "afkbot").mkdir(parents=True, exist_ok=True)
    (staged_source / "pyproject.toml").write_text("[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8")
    next_app_dir = install_dir / "app" / "release-1"
    python_target = tmp_path / "python-target"
    python_target.write_text("", encoding="utf-8")
    python_link = tmp_path / "venv-python"
    python_link.symlink_to(python_target)
    commands: list[list[str]] = []
    afk_calls: list[tuple[str, ...]] = []

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(runtime_root))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{runtime_root / 'afkbot.db'}")
    monkeypatch.setenv(MANAGED_INSTALL_DIR_ENV, str(install_dir))
    monkeypatch.setenv(MANAGED_RUNTIME_DIR_ENV, str(runtime_root))
    monkeypatch.setenv(MANAGED_APP_DIR_ENV, str(current_app_dir))
    monkeypatch.setenv(MANAGED_SOURCE_URL_ENV, f"file://{tmp_path / 'source'}")
    monkeypatch.setenv(MANAGED_SOURCE_REF_ENV, "main")
    get_settings.cache_clear()
    settings = get_settings()

    def _fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        error_code: str,
        fallback: str,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, error_code, fallback
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime.stage_source_snapshot", lambda context: staged_source)
    monkeypatch.setattr("afkbot.services.update_runtime.build_next_app_dir", lambda context: next_app_dir)
    monkeypatch.setattr("afkbot.services.update_runtime.sys.executable", str(python_link))
    monkeypatch.setattr("afkbot.services.update_runtime._run_checked", _fake_run_checked)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_subcommand",
        lambda *, settings, args: afk_calls.append(args),  # type: ignore[no-untyped-call]
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service",
        lambda: True,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._wait_for_local_health",
        lambda *, settings, timeout_sec=90.0: True,
    )

    # Act
    result = run_update(settings)

    # Assert
    assert result.install_mode == "managed"
    assert result.source_updated is True
    assert result.runtime_restarted is True
    assert next_app_dir.exists()
    assert [sys.executable, "-m", "pip", "install", "-e", str(next_app_dir)] in commands
    assert afk_calls == [
        ("doctor", "--no-integrations", "--no-upgrades"),
        ("upgrade", "apply", "--quiet"),
    ]
    launcher_name = "afk.cmd" if os.name == "nt" else "afk"
    launcher_text = (install_dir / "bin" / launcher_name).read_text(encoding="utf-8")
    metadata_payload = json.loads((install_dir / "managed-install.json").read_text(encoding="utf-8"))
    assert MANAGED_RUNTIME_DIR_ENV in launcher_text
    assert MANAGED_METADATA_PATH_ENV in launcher_text
    assert str(runtime_root) not in launcher_text
    assert str(next_app_dir) not in launcher_text
    assert str(python_link) not in launcher_text
    assert metadata_payload["runtime_dir"] == str(runtime_root)
    assert metadata_payload["app_dir"] == str(next_app_dir)


def test_run_update_uses_checkout_root_when_runtime_root_is_separate(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Host update should target the active source checkout when runtime root is separate."""

    # Arrange
    runtime_root = tmp_path / "runtime"
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir(parents=True, exist_ok=True)
    (checkout_root / ".git").mkdir()
    (checkout_root / "pyproject.toml").write_text("[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(runtime_root))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{runtime_root / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    commands: list[list[str]] = []

    def _fake_run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        commands.append(command)
        if command[:4] == ["git", "-C", str(checkout_root), "remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr="")
        if command[:6] == ["git", "-C", str(checkout_root), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == ["git", "-C", str(checkout_root), "diff", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == ["git", "-C", str(checkout_root), "diff", "--cached", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="same-sha\n", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "rev-parse", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="same-sha\n", stderr="")
        if command[:7] == ["git", "-C", str(checkout_root), "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", checkout_root)
    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr("afkbot.services.update_runtime._run_afk_subcommand", lambda *, settings, args: None)
    monkeypatch.setattr("afkbot.services.update_runtime._restart_managed_host_runtime_service", lambda: False)

    # Act
    result = run_update(settings)

    # Assert
    assert result.install_mode == "host"
    assert result.source_updated is False
    assert ["git", "-C", str(checkout_root), "fetch", "--depth", "1", "--no-tags", "origin", "main"] in commands
    assert all(str(runtime_root) not in " ".join(command) for command in commands if command and command[0] == "git")


def test_run_update_upgrades_uv_tool_install(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Installed uv-tool mode should upgrade the tool, then run maintenance via the fresh executable."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    uv_executable = tool_bin / ("uv.exe" if os.name == "nt" else "uv")
    uv_executable.write_text("", encoding="utf-8")
    afk_executable = tool_bin / ("afk.cmd" if os.name == "nt" else "afk")
    afk_executable.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    def _fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        error_code: str,
        fallback: str,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, error_code, fallback
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path / "installed-tool")
    monkeypatch.setattr("afkbot.services.update_runtime._resolve_uv_executable", lambda: uv_executable)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_tool_afk_executable",
        lambda *, uv_executable: afk_executable,
    )
    monkeypatch.setattr("afkbot.services.update_runtime._run_checked", _fake_run_checked)
    monkeypatch.setattr("afkbot.services.update_runtime._restart_managed_host_runtime_service", lambda: False)

    result = run_update(settings)

    assert result.install_mode == "uv-tool"
    assert result.source_updated is True
    assert result.maintenance_applied is True
    assert result.runtime_restarted is False
    assert [str(uv_executable), "tool", "upgrade", "afkbotio", "--reinstall"] in commands
    assert [str(afk_executable), "upgrade", "apply", "--quiet"] in commands
    assert [str(afk_executable), "doctor", "--no-integrations", "--no-upgrades"] in commands


def test_run_update_cleans_staged_source_when_parent_creation_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Managed update should clean staged source when app-root creation fails."""

    # Arrange
    runtime_root = tmp_path / "runtime"
    install_dir = tmp_path / "managed"
    current_app_dir = install_dir / "app" / "current"
    current_app_dir.mkdir(parents=True, exist_ok=True)
    staged_source = tmp_path / "staged" / "source"
    staged_source.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(runtime_root))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{runtime_root / 'afkbot.db'}")
    monkeypatch.setenv(MANAGED_INSTALL_DIR_ENV, str(install_dir))
    monkeypatch.setenv(MANAGED_RUNTIME_DIR_ENV, str(runtime_root))
    monkeypatch.setenv(MANAGED_APP_DIR_ENV, str(current_app_dir))
    monkeypatch.setenv(MANAGED_SOURCE_URL_ENV, "file:///tmp/source")
    monkeypatch.setenv(MANAGED_SOURCE_REF_ENV, "main")
    get_settings.cache_clear()
    settings = get_settings()
    cleanup_calls: list[Path] = []
    next_app_dir = install_dir / "app" / "release-1"
    real_mkdir = Path.mkdir

    def _fake_mkdir(self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
        if self == next_app_dir.parent:
            raise OSError("disk full")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr("afkbot.services.update_runtime.stage_source_snapshot", lambda context: staged_source)
    monkeypatch.setattr("afkbot.services.update_runtime.build_next_app_dir", lambda context: next_app_dir)
    monkeypatch.setattr("afkbot.services.update_runtime.cleanup_staged_source", lambda path: cleanup_calls.append(path))
    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)

    # Act / Assert
    with pytest.raises(UpdateRuntimeError) as exc:
        run_update(settings)

    assert "failed to stage managed source snapshot" in exc.value.reason
    assert cleanup_calls == [staged_source]


def test_wait_for_local_health_uses_persisted_runtime_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Health polling should target the persisted runtime port written by setup."""

    # Arrange
    settings = _prepare_settings(tmp_path, monkeypatch)
    write_runtime_config(
        settings,
        config={
            "runtime_host": "127.0.0.1",
            "runtime_port": 19000,
        },
    )
    captured: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    def _fake_urlopen(url: str, timeout: int) -> _FakeResponse:
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("afkbot.services.update_runtime.urlopen", _fake_urlopen)

    # Act
    result = _wait_for_local_health(settings=settings)

    # Assert
    assert result is True
    assert captured == {
        "url": "http://127.0.0.1:19001/healthz",
        "timeout": 5,
    }
