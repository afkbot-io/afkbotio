"""Tests for managed runtime update service."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import tomllib
from urllib.error import HTTPError

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
from afkbot.services.update_runtime import (
    _build_noninteractive_git_env,
    _run_afk_executable_with_env,
    _resolve_uv_tool_afk_executable,
    _wait_for_local_health,
    format_update_success_for_language,
    inspect_available_update,
)
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
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8"
    )
    commands: list[list[str]] = []
    afk_calls: list[tuple[str, ...]] = []
    rev_parse_values = iter(["before-sha", "before-sha", "after-sha"])

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        commands.append(command)
        if command[:4] == ["git", "-C", str(tmp_path), "remote"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr=""
            )
        if command[:6] == ["git", "-C", str(tmp_path), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == [
            "git",
            "-C",
            str(tmp_path),
            "diff",
            "--cached",
            "--quiet",
            "--ignore-submodules",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{next(rev_parse_values)}\n", stderr=""
            )
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="after-sha\n", stderr="")
        if command[:7] == [
            "git",
            "-C",
            str(tmp_path),
            "merge-base",
            "--is-ancestor",
            "HEAD",
            "FETCH_HEAD",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_executable",
        lambda: tmp_path / ("uv.exe" if os.name == "nt" else "uv"),
    )
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
        ("doctor", "--no-integrations", "--no-upgrades", "--no-daemon"),
        ("upgrade", "apply", "--quiet"),
    ]
    assert [
        "git",
        "-C",
        str(tmp_path),
        "fetch",
        "--depth",
        "1",
        "--no-tags",
        "origin",
        "main",
    ] in commands
    assert ["git", "-C", str(tmp_path), "merge", "--ff-only", "FETCH_HEAD"] in commands
    assert [
        str(tmp_path / ("uv.exe" if os.name == "nt" else "uv")),
        "pip",
        "install",
        "--python",
        sys.executable,
        "--editable",
        str(tmp_path),
    ] in commands


def test_run_update_resets_checkout_after_history_rewrite(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Local update should hard-reset when remote history was rewritten."""

    # Arrange
    settings = _prepare_settings(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8"
    )
    commands: list[list[str]] = []
    afk_calls: list[tuple[str, ...]] = []
    rev_parse_values = iter(["before-sha", "before-sha", "after-sha"])

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        commands.append(command)
        if command[:4] == ["git", "-C", str(tmp_path), "remote"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr=""
            )
        if command[:6] == ["git", "-C", str(tmp_path), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == ["git", "-C", str(tmp_path), "diff", "--quiet", "--ignore-submodules"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == [
            "git",
            "-C",
            str(tmp_path),
            "diff",
            "--cached",
            "--quiet",
            "--ignore-submodules",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{next(rev_parse_values)}\n", stderr=""
            )
        if command[:5] == ["git", "-C", str(tmp_path), "rev-parse", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="rewritten-sha\n", stderr="")
        if command[:7] == [
            "git",
            "-C",
            str(tmp_path),
            "merge-base",
            "--is-ancestor",
            "HEAD",
            "FETCH_HEAD",
        ]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_executable",
        lambda: tmp_path / ("uv.exe" if os.name == "nt" else "uv"),
    )
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
    assert ("doctor", "--no-integrations", "--no-upgrades", "--no-daemon") in afk_calls
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
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8"
    )

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        if command[:4] == ["git", "-C", str(tmp_path), "remote"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr=""
            )
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
    (staged_source / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8"
    )
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
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, error_code, fallback, timeout_sec, env
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "afkbot.services.update_runtime.stage_source_snapshot", lambda context: staged_source
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.build_next_app_dir", lambda context: next_app_dir
    )
    monkeypatch.setattr("afkbot.services.update_runtime.sys.executable", str(python_link))
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_executable",
        lambda: tmp_path / ("uv.exe" if os.name == "nt" else "uv"),
    )
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
    assert [
        str(tmp_path / ("uv.exe" if os.name == "nt" else "uv")),
        "pip",
        "install",
        "--python",
        str(python_link),
        "--editable",
        str(next_app_dir),
    ] in commands
    assert afk_calls == [
        ("doctor", "--no-integrations", "--no-upgrades", "--no-daemon"),
        ("upgrade", "apply", "--quiet"),
    ]
    launcher_name = "afk.cmd" if os.name == "nt" else "afk"
    launcher_text = (install_dir / "bin" / launcher_name).read_text(encoding="utf-8")
    metadata_payload = json.loads(
        (install_dir / "managed-install.json").read_text(encoding="utf-8")
    )
    assert MANAGED_RUNTIME_DIR_ENV in launcher_text
    assert MANAGED_METADATA_PATH_ENV in launcher_text
    assert str(runtime_root) not in launcher_text
    assert str(next_app_dir) not in launcher_text
    assert str(python_link) not in launcher_text
    assert metadata_payload["runtime_dir"] == str(runtime_root)
    assert metadata_payload["app_dir"] == str(next_app_dir)


def test_run_afk_executable_with_env_creates_missing_runtime_root(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Bootstrap replay should create the runtime cwd before spawning AFKBOT."""

    runtime_root = tmp_path / "missing" / "runtime-root"
    settings = _prepare_settings(runtime_root, monkeypatch)
    executable = tmp_path / ("afk.exe" if os.name == "nt" else "afk")
    executable.write_text("", encoding="utf-8")
    calls: list[tuple[list[str], str]] = []

    def _fake_subprocess_run(
        command: list[str],
        *,
        cwd: str,
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text, env
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime.subprocess.run", _fake_subprocess_run)

    _run_afk_executable_with_env(
        executable=executable,
        settings=settings,
        args=("setup", "--bootstrap-only", "--yes", "--lang", "en"),
        env=dict(os.environ),
    )

    assert runtime_root.is_dir()
    assert calls == [
        ([str(executable), "setup", "--bootstrap-only", "--yes", "--lang", "en"], str(runtime_root))
    ]


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
    (checkout_root / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8"
    )
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(runtime_root))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{runtime_root / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    commands: list[list[str]] = []

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        commands.append(command)
        if command[:4] == ["git", "-C", str(checkout_root), "remote"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="git@github.com:afkbot-io/afkbotio.git\n", stderr=""
            )
        if command[:6] == ["git", "-C", str(checkout_root), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == [
            "git",
            "-C",
            str(checkout_root),
            "diff",
            "--quiet",
            "--ignore-submodules",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == [
            "git",
            "-C",
            str(checkout_root),
            "diff",
            "--cached",
            "--quiet",
            "--ignore-submodules",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="same-sha\n", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "rev-parse", "FETCH_HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="same-sha\n", stderr="")
        if command[:7] == [
            "git",
            "-C",
            str(checkout_root),
            "merge-base",
            "--is-ancestor",
            "HEAD",
            "FETCH_HEAD",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", checkout_root)
    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_subcommand", lambda *, settings, args: None
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service", lambda: False
    )

    # Act
    result = run_update(settings)

    # Assert
    assert result.install_mode == "host"
    assert result.source_updated is False
    assert [
        "git",
        "-C",
        str(checkout_root),
        "fetch",
        "--depth",
        "1",
        "--no-tags",
        "origin",
        "main",
    ] in commands
    assert all(
        str(runtime_root) not in " ".join(command)
        for command in commands
        if command and command[0] == "git"
    )


def test_run_update_upgrades_uv_tool_install(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Installed uv-tool mode should replay the default package source, then run maintenance."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    uv_executable = tool_bin / ("uv.exe" if os.name == "nt" else "uv")
    uv_executable.write_text("", encoding="utf-8")
    afk_executable = tool_bin / ("afk.exe" if os.name == "nt" else "afk")
    afk_executable.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    shell_commands: list[list[str]] = []
    bootstrap_calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def _fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        error_code: str,
        fallback: str,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, error_code, fallback, timeout_sec, env
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        shell_commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _fake_bootstrap(
        *,
        executable: Path,
        settings: object,
        args: tuple[str, ...],
        env: dict[str, str],
    ) -> None:
        del executable, settings
        bootstrap_calls.append((args, dict(env)))

    monkeypatch.setattr(
        "afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path / "installed-tool"
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_executable", lambda: uv_executable
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_tool_afk_executable",
        lambda *, uv_executable: afk_executable,
    )
    monkeypatch.setattr("afkbot.services.update_runtime._run_checked", _fake_run_checked)
    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_executable_with_env",
        _fake_bootstrap,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service", lambda: False
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.resolve_install_source_target",
        lambda install_source: "1.2.3",
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.setup_is_complete",
        lambda settings: True,
    )

    result = run_update(settings)

    assert result.install_mode == "uv-tool"
    assert result.source_updated is True
    assert result.maintenance_applied is True
    assert result.runtime_restarted is False
    assert [
        str(uv_executable),
        "tool",
        "install",
        "--python",
        "3.12",
        "--reinstall",
        "afkbotio",
    ] in commands
    assert [str(uv_executable), "tool", "update-shell"] in shell_commands
    assert len(bootstrap_calls) == 1
    bootstrap_args, bootstrap_env = bootstrap_calls[0]
    assert bootstrap_args == ("setup", "--bootstrap-only", "--yes", "--lang", "en")
    assert bootstrap_env["AFKBOT_INSTALL_SOURCE_MODE"] == "package"
    assert bootstrap_env["AFKBOT_INSTALL_SOURCE_SPEC"] == "afkbotio"
    assert bootstrap_env["AFKBOT_INSTALL_SOURCE_RESOLVED_TARGET"] == "1.2.3"
    assert [str(afk_executable), "upgrade", "apply", "--quiet"] in commands
    assert [
        str(afk_executable),
        "doctor",
        "--no-integrations",
        "--no-upgrades",
        "--no-daemon",
    ] in commands


def test_run_update_prefers_saved_installer_source_over_git_checkout(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Installer metadata should keep editable/archive tool installs off the git-fetch path."""

    settings = _prepare_settings(tmp_path / "runtime", monkeypatch)
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir(parents=True, exist_ok=True)
    (checkout_root / ".git").mkdir()
    (checkout_root / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8"
    )
    source_path = tmp_path / "editable-source"
    source_path.mkdir(parents=True, exist_ok=True)
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    uv_executable = tool_bin / ("uv.exe" if os.name == "nt" else "uv")
    uv_executable.write_text("", encoding="utf-8")
    afk_executable = tool_bin / ("afk.exe" if os.name == "nt" else "afk")
    afk_executable.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    shell_commands: list[list[str]] = []
    bootstrap_calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    write_runtime_config(
        settings,
        config={
            "install_source_mode": "editable",
            "install_source_spec": str(source_path),
        },
    )

    def _fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        error_code: str,
        fallback: str,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, error_code, fallback, timeout_sec, env
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        shell_commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _fake_bootstrap(
        *,
        executable: Path,
        settings: object,
        args: tuple[str, ...],
        env: dict[str, str],
    ) -> None:
        del executable, settings
        bootstrap_calls.append((args, dict(env)))

    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", checkout_root)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_executable", lambda: uv_executable
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_tool_afk_executable",
        lambda *, uv_executable: afk_executable,
    )
    monkeypatch.setattr("afkbot.services.update_runtime._run_checked", _fake_run_checked)
    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_executable_with_env",
        _fake_bootstrap,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service", lambda: False
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.resolve_install_source_target",
        lambda install_source: None,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.setup_is_complete",
        lambda settings: True,
    )

    result = run_update(settings)

    assert result.install_mode == "uv-tool"
    assert [
        str(uv_executable),
        "tool",
        "install",
        "--python",
        "3.12",
        "--reinstall",
        "--editable",
        str(source_path),
    ] in commands
    assert [str(uv_executable), "tool", "update-shell"] in shell_commands
    assert len(bootstrap_calls) == 1
    bootstrap_args, bootstrap_env = bootstrap_calls[0]
    assert bootstrap_args == ("setup", "--bootstrap-only", "--yes", "--lang", "en")
    assert bootstrap_env["AFKBOT_INSTALL_SOURCE_MODE"] == "editable"
    assert bootstrap_env["AFKBOT_INSTALL_SOURCE_SPEC"] == str(source_path)
    assert "AFKBOT_INSTALL_SOURCE_RESOLVED_TARGET" not in bootstrap_env
    assert all(not command or command[0] != "git" for command in commands)


def test_run_update_skips_doctor_for_uv_tool_install_before_setup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Fresh uv-tool installs should update even when setup is not completed yet."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    uv_executable = tool_bin / ("uv.exe" if os.name == "nt" else "uv")
    uv_executable.write_text("", encoding="utf-8")
    afk_executable = tool_bin / ("afk.exe" if os.name == "nt" else "afk")
    afk_executable.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    shell_commands: list[list[str]] = []
    bootstrap_calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def _fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        error_code: str,
        fallback: str,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, error_code, fallback, timeout_sec, env
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_sec, env
        shell_commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _fake_bootstrap(
        *,
        executable: Path,
        settings: object,
        args: tuple[str, ...],
        env: dict[str, str],
    ) -> None:
        del executable, settings
        bootstrap_calls.append((args, dict(env)))

    monkeypatch.setattr(
        "afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", tmp_path / "installed-tool"
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_executable", lambda: uv_executable
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_tool_afk_executable",
        lambda *, uv_executable: afk_executable,
    )
    monkeypatch.setattr("afkbot.services.update_runtime._run_checked", _fake_run_checked)
    monkeypatch.setattr("afkbot.services.update_runtime._run_command", _fake_run_command)
    monkeypatch.setattr(
        "afkbot.services.update_runtime._run_afk_executable_with_env",
        _fake_bootstrap,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._restart_managed_host_runtime_service", lambda: False
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.resolve_install_source_target",
        lambda install_source: "1.4.0",
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.setup_is_complete",
        lambda settings: False,
    )

    result = run_update(settings)

    assert result.install_mode == "uv-tool"
    assert [str(afk_executable), "upgrade", "apply", "--quiet"] in commands
    assert [
        str(afk_executable),
        "doctor",
        "--no-integrations",
        "--no-upgrades",
        "--no-daemon",
    ] not in commands
    assert "Doctor: skipped until `afk setup` completes" in result.details
    assert len(bootstrap_calls) == 1
    assert [str(uv_executable), "tool", "update-shell"] in shell_commands


def test_inspect_available_update_uses_saved_installer_target_without_git_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Archive installs should detect updates from saved installer metadata even without `.git`."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    write_runtime_config(
        settings,
        config={
            "install_source_mode": "archive",
            "install_source_spec": "https://github.com/afkbot-io/afkbotio/archive/main.tar.gz",
            "install_source_resolved_target": "oldsha123456",
        },
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.resolve_install_source_target",
        lambda install_source: "newsha654321",
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.load_cli_version_info",
        lambda root_dir=None: type(
            "_Version",
            (),
            {
                "version": "1.0.10",
                "git_sha": None,
                "render": lambda self: "afk 1.0.10",
            },
        )(),
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._is_source_checkout_install",
        lambda: False,
    )

    availability = inspect_available_update(settings)

    assert availability is not None
    assert availability.install_mode == "uv-tool"
    assert availability.target_id == "github:afkbot-io/afkbotio@main:newsha654321"


def test_inspect_available_update_ignores_host_git_fetch_timeout(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Chat update checks should fail open when host-checkout fetch blocks or times out."""

    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setenv("GCM_INTERACTIVE", "always")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -oBatchMode=no -i /tmp/key")
    settings = _prepare_settings(tmp_path / "runtime", monkeypatch)
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir(parents=True, exist_ok=True)
    (checkout_root / ".git").mkdir()
    (checkout_root / "pyproject.toml").write_text(
        "[project]\nname='afkbot'\nversion='1.0.0'\n",
        encoding="utf-8",
    )
    subprocess_calls: list[dict[str, object]] = []

    def _fake_subprocess_run(
        command: list[str],
        *,
        cwd: str | None = None,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        subprocess_calls.append(
            {
                "command": command,
                "timeout": timeout,
                "env": env,
            }
        )
        if command[:4] == ["git", "-C", str(checkout_root), "remote"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="git@github.com:afkbot-io/afkbotio.git\n",
                stderr="",
            )
        if command[:6] == ["git", "-C", str(checkout_root), "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:6] == [
            "git",
            "-C",
            str(checkout_root),
            "diff",
            "--quiet",
            "--ignore-submodules",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:6] == [
            "git",
            "-C",
            str(checkout_root),
            "diff",
            "--cached",
            "--quiet",
            "--ignore-submodules",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "ls-files", "--others"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:5] == ["git", "-C", str(checkout_root), "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="current-sha\n", stderr="")
        if command[:4] == ["git", "-C", str(checkout_root), "fetch"]:
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout or 0.0)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.update_runtime._CODE_CHECKOUT_ROOT", checkout_root)
    monkeypatch.setattr("afkbot.services.update_runtime.subprocess.run", _fake_subprocess_run)

    availability = inspect_available_update(settings)

    assert availability is None
    fetch_call = next(
        call
        for call in subprocess_calls
        if call["command"][:4] == ["git", "-C", str(checkout_root), "fetch"]
    )
    assert isinstance(fetch_call["timeout"], float)
    env = fetch_call["env"]
    assert isinstance(env, dict)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "BatchMode=no" not in env["GIT_SSH_COMMAND"]


def test_noninteractive_git_env_uses_valid_default_ssh_command(
    monkeypatch: MonkeyPatch,
) -> None:
    """Default GIT_SSH_COMMAND should include an executable, not only ssh options."""

    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)

    env = _build_noninteractive_git_env()

    assert env["GIT_SSH_COMMAND"] == "ssh -oBatchMode=yes"


def test_inspect_available_update_uses_package_source_without_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Legacy uv-tool installs without saved metadata should fall back to the PyPI package source."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.services.update_runtime.resolve_install_source_target",
        lambda install_source: "1.4.0",
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.load_cli_version_info",
        lambda root_dir=None: type(
            "_Version",
            (),
            {
                "version": "1.0.10",
                "git_sha": None,
                "render": lambda self: "afk 1.0.10",
            },
        )(),
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._is_source_checkout_install",
        lambda: False,
    )

    availability = inspect_available_update(settings)

    assert availability is not None
    assert availability.install_mode == "uv-tool"
    assert availability.target_id == "package:afkbotio:1.4.0"
    assert availability.target_label == "afkbotio 1.4.0"


def test_inspect_available_update_ignores_uv_tool_http_404(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Chat update checks should not crash when PyPI lookup returns 404."""

    settings = _prepare_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.services.update_runtime.resolve_managed_install_context",
        lambda: None,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.read_install_source_from_runtime_config",
        lambda runtime_config: None,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._is_source_checkout_install",
        lambda: False,
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime._fetch_json_payload",
        lambda url: (_ for _ in ()).throw(
            HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)
        ),
    )

    assert inspect_available_update(settings) is None


def test_format_update_success_for_language_renders_russian_copy() -> None:
    """Localized update summaries should stay fully Russian in chat update flows."""

    rendered = format_update_success_for_language(
        result=type(
            "_Result",
            (),
            {
                "install_mode": "host",
                "source_updated": True,
                "runtime_restarted": False,
                "maintenance_applied": True,
                "details": (
                    "Git branch: main",
                    "Runtime health: ok",
                    "Managed host service not found; restart manually with `afk start`",
                ),
            },
        )(),
        lang="ru",
    )

    assert "Обновление AFKBOT завершено." in rendered
    assert "Режим установки: host" in rendered
    assert "Источник: обновлён" in rendered
    assert "Обслуживание: выполнено" in rendered
    assert "Runtime: без managed-перезапуска" in rendered
    assert "Git-ветка: main" in rendered
    assert "Состояние runtime: ok" in rendered
    assert "перезапустите вручную через `afk start`" in rendered


def test_project_declares_packaging_runtime_dependency() -> None:
    """Installer metadata must include packaging for update-runtime imports."""

    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        payload = tomllib.load(handle)

    project = payload.get("project")
    assert isinstance(project, dict)
    dependencies = project.get("dependencies")
    assert isinstance(dependencies, list)
    assert any(
        isinstance(dependency, str) and dependency.startswith("packaging")
        for dependency in dependencies
    )


def test_resolve_uv_tool_afk_executable_prefers_windows_exe(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Windows uv-tool resolution should prefer the generated .exe launcher."""

    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    uv_executable = tool_bin / "uv.exe"
    uv_executable.write_text("", encoding="utf-8")
    afk_executable = tool_bin / "afk.exe"
    afk_executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_tool_bin_dir", lambda *, uv_executable: tool_bin
    )
    monkeypatch.setattr("afkbot.services.update_runtime.os.name", "nt", raising=False)

    result = _resolve_uv_tool_afk_executable(uv_executable=uv_executable)

    assert result == afk_executable


def test_resolve_uv_tool_afk_executable_falls_back_to_windows_cmd(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Windows uv-tool resolution should tolerate legacy .cmd launchers as a fallback."""

    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    uv_executable = tool_bin / "uv.exe"
    uv_executable.write_text("", encoding="utf-8")
    afk_executable = tool_bin / "afk.cmd"
    afk_executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "afkbot.services.update_runtime._resolve_uv_tool_bin_dir", lambda *, uv_executable: tool_bin
    )
    monkeypatch.setattr("afkbot.services.update_runtime.os.name", "nt", raising=False)

    result = _resolve_uv_tool_afk_executable(uv_executable=uv_executable)

    assert result == afk_executable


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

    def _fake_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self == next_app_dir.parent:
            raise OSError("disk full")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(
        "afkbot.services.update_runtime.stage_source_snapshot", lambda context: staged_source
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.build_next_app_dir", lambda context: next_app_dir
    )
    monkeypatch.setattr(
        "afkbot.services.update_runtime.cleanup_staged_source",
        lambda path: cleanup_calls.append(path),
    )
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
