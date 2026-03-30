"""Tests for managed self-hosted install helpers."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
from pytest import MonkeyPatch

from afkbot.services.managed_install import (
    MANAGED_APP_DIR_ENV,
    MANAGED_INSTALL_DIR_ENV,
    MANAGED_METADATA_PATH_ENV,
    MANAGED_RUNTIME_DIR_ENV,
    MANAGED_SOURCE_REF_ENV,
    MANAGED_SOURCE_URL_ENV,
    ManagedInstallContext,
    _safe_extract_tar,
    _render_windows_launcher,
    cleanup_staged_source,
    pick_convenience_launcher_path,
    resolve_managed_install_context,
    stage_source_snapshot,
    write_managed_launcher,
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _legacy_unix_install_dir(home_dir: Path) -> Path:
    if sys.platform == "darwin":
        return home_dir / "Library" / "Application Support" / "AFKBOT"
    return home_dir / ".local" / "share" / "afkbot"


def test_resolve_managed_install_context_requires_complete_env(monkeypatch: MonkeyPatch) -> None:
    """Managed install context should fail closed when wrapper metadata is incomplete."""

    # Arrange
    monkeypatch.delenv(MANAGED_INSTALL_DIR_ENV, raising=False)
    monkeypatch.delenv(MANAGED_RUNTIME_DIR_ENV, raising=False)
    monkeypatch.delenv(MANAGED_APP_DIR_ENV, raising=False)
    monkeypatch.delenv(MANAGED_SOURCE_URL_ENV, raising=False)
    monkeypatch.delenv(MANAGED_SOURCE_REF_ENV, raising=False)

    # Act
    result = resolve_managed_install_context()

    # Assert
    assert result is None


def test_stage_source_snapshot_copies_local_source_without_git_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Local managed source staging should copy the app tree without `.git` metadata."""

    # Arrange
    source_root = tmp_path / "source"
    (source_root / "afkbot").mkdir(parents=True, exist_ok=True)
    (source_root / ".git").mkdir(parents=True, exist_ok=True)
    (source_root / "pyproject.toml").write_text("[project]\nname='afkbot'\nversion='1.0.0'\n", encoding="utf-8")
    (source_root / "afkbot" / "__init__.py").write_text("__all__ = ()\n", encoding="utf-8")
    monkeypatch.setenv(MANAGED_INSTALL_DIR_ENV, str(tmp_path / "install"))
    monkeypatch.setenv(MANAGED_RUNTIME_DIR_ENV, str(tmp_path / "runtime"))
    monkeypatch.setenv(MANAGED_APP_DIR_ENV, str(tmp_path / "install" / "app" / "current"))
    monkeypatch.setenv(MANAGED_SOURCE_URL_ENV, str(source_root))
    monkeypatch.setenv(MANAGED_SOURCE_REF_ENV, "main")
    context = resolve_managed_install_context()
    assert context is not None

    # Act
    staged_root = stage_source_snapshot(context)

    # Assert
    assert (staged_root / "pyproject.toml").exists()
    assert (staged_root / "afkbot" / "__init__.py").exists()
    assert not (staged_root / ".git").exists()
    cleanup_staged_source(staged_root)


def test_resolve_managed_install_context_reads_metadata_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Managed install context should load persisted metadata when direct env is absent."""

    # Arrange
    metadata_path = tmp_path / "managed-install.json"
    metadata_path.write_text(
        json.dumps(
            {
                "install_dir": str(tmp_path / "install"),
                "runtime_dir": str(tmp_path / "runtime"),
                "app_dir": str(tmp_path / "install" / "app" / "release-1"),
                "source_url": "https://github.com/afkbot-io/afkbotio.git",
                "source_ref": "main",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv(MANAGED_INSTALL_DIR_ENV, raising=False)
    monkeypatch.delenv(MANAGED_RUNTIME_DIR_ENV, raising=False)
    monkeypatch.delenv(MANAGED_APP_DIR_ENV, raising=False)
    monkeypatch.delenv(MANAGED_SOURCE_URL_ENV, raising=False)
    monkeypatch.delenv(MANAGED_SOURCE_REF_ENV, raising=False)
    monkeypatch.setenv(MANAGED_METADATA_PATH_ENV, str(metadata_path))

    # Act
    result = resolve_managed_install_context()

    # Assert
    assert result is not None
    assert result.install_dir == (tmp_path / "install").resolve(strict=False)
    assert result.runtime_dir == (tmp_path / "runtime").resolve(strict=False)
    assert result.app_dir == (tmp_path / "install" / "app" / "release-1").resolve(strict=False)
    assert result.source_url == "https://github.com/afkbot-io/afkbotio.git"
    assert result.source_ref == "main"


def test_write_managed_launcher_persists_metadata_and_keeps_user_strings_out_of_script(
    tmp_path: Path,
) -> None:
    """Managed launcher should read trusted metadata instead of embedding raw source strings."""

    # Arrange
    install_dir = tmp_path / "install"
    context = ManagedInstallContext(
        install_dir=install_dir,
        runtime_dir=tmp_path / "runtime",
        app_dir=install_dir / "app" / "release-1",
        source_url='https://example.invalid/repo"$(touch pwned)".git',
        source_ref='main"; rm -rf / #',
    )
    python_target = tmp_path / "python-target"
    python_target.write_text("", encoding="utf-8")
    python_executable = install_dir / "venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True, exist_ok=True)
    python_executable.symlink_to(python_target)

    # Act
    launcher_path = write_managed_launcher(
        context=context,
        python_executable=python_executable,
        app_dir=context.app_dir,
    )

    # Assert
    launcher_content = launcher_path.read_text(encoding="utf-8")
    metadata_payload = json.loads(context.metadata_path.read_text(encoding="utf-8"))
    assert MANAGED_RUNTIME_DIR_ENV in launcher_content
    assert MANAGED_METADATA_PATH_ENV in launcher_content
    assert context.source_url not in launcher_content
    assert context.source_ref not in launcher_content
    assert str(python_target) not in launcher_content
    assert metadata_payload["source_url"] == context.source_url
    assert metadata_payload["source_ref"] == context.source_ref


def test_pick_convenience_launcher_path_prefers_writable_path_entry(tmp_path: Path) -> None:
    """Managed install should expose `afk` through one writable active PATH entry when available."""

    # Arrange
    launcher_dir = tmp_path / "install" / "bin"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = launcher_dir / "afk"
    launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    preferred_dir = tmp_path / "usr-local-bin"
    preferred_dir.mkdir(parents=True, exist_ok=True)
    fallback_dir = tmp_path / "home-bin"
    fallback_dir.mkdir(parents=True, exist_ok=True)

    # Act
    result = pick_convenience_launcher_path(
        launcher_path=launcher_path,
        path_env=os.pathsep.join((str(preferred_dir), str(fallback_dir))),
    )

    # Assert
    assert result == preferred_dir / "afk"


def test_pick_convenience_launcher_path_skips_foreign_existing_binary(tmp_path: Path) -> None:
    """Managed install should not overwrite another `afk` already present in PATH."""

    # Arrange
    launcher_dir = tmp_path / "install" / "bin"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = launcher_dir / "afk"
    launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    foreign_dir = tmp_path / "usr-local-bin"
    foreign_dir.mkdir(parents=True, exist_ok=True)
    (foreign_dir / "afk").write_text("foreign", encoding="utf-8")
    next_dir = tmp_path / "home-bin"
    next_dir.mkdir(parents=True, exist_ok=True)

    # Act
    result = pick_convenience_launcher_path(
        launcher_path=launcher_path,
        path_env=os.pathsep.join((str(foreign_dir), str(next_dir))),
    )

    # Assert
    assert result == next_dir / "afk"


def test_pick_convenience_launcher_path_reuses_broken_symlink_slot(tmp_path: Path) -> None:
    """Managed install should reuse a broken `afk` symlink so reinstall restores the command."""

    # Arrange
    launcher_dir = tmp_path / "install" / "bin"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = launcher_dir / "afk"
    launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    path_dir = tmp_path / "usr-local-bin"
    path_dir.mkdir(parents=True, exist_ok=True)
    (path_dir / "afk").symlink_to(tmp_path / "missing-afk")

    # Act
    result = pick_convenience_launcher_path(
        launcher_path=launcher_path,
        path_env=str(path_dir),
    )

    # Assert
    assert result == path_dir / "afk"


def test_shell_installer_dry_run_uses_uv_tool_install(tmp_path: Path) -> None:
    """Shell installer dry-run should emit uv-tool operations without managed-venv steps."""

    # Arrange
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "install.sh"
    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--dry-run",
            "--repo-url",
            f"file://{repo_root}",
            "--git-ref",
            "local-dry-run",
            "--skip-setup",
        ],
        capture_output=True,
        check=False,
        cwd=repo_root,
        text=True,
    )

    # Assert
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0
    assert "virtualenv python not found" not in output
    assert "tool install --python 3.12 --reinstall --editable" in output
    assert "tool update-shell" in output


def test_shell_installer_dry_run_uses_github_archive_source_for_remote_repo() -> None:
    """Shell installer should use a GitHub archive URL so hosted installs do not depend on Git."""

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "install.sh"
    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--dry-run",
            "--repo-url",
            "https://github.com/afkbot-io/afkbotio.git",
            "--git-ref",
            "main",
            "--skip-setup",
        ],
        capture_output=True,
        check=False,
        cwd=repo_root,
        text=True,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0
    assert "git+https://github.com/afkbot-io/afkbotio.git@main" not in output
    assert "https://github.com/afkbot-io/afkbotio/archive/main.tar.gz" in output
    assert "tool install --python 3.12 --reinstall" in output


def test_shell_installer_dry_run_warns_when_current_shell_path_will_still_miss_afk(
    tmp_path: Path,
) -> None:
    """Shell installer should warn when PATH changes only apply after the shell reloads."""

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "install.sh"
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["PATH"] = "/usr/bin:/bin"
    env.pop("XDG_BIN_HOME", None)
    env.pop("XDG_DATA_HOME", None)

    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--dry-run",
            "--repo-url",
            f"file://{repo_root}",
            "--git-ref",
            "local-dry-run",
            "--skip-setup",
        ],
        capture_output=True,
        check=False,
        cwd=repo_root,
        env=env,
        text=True,
    )

    output = f"{result.stdout}\n{result.stderr}"
    expected_bin_dir = home_dir / ".local" / "bin"

    assert result.returncode == 0
    assert "If `afk` is not visible in the current shell yet" in output
    assert f'export PATH="{expected_bin_dir}:$PATH"' in output


def test_shell_installer_preserves_legacy_integration_when_uv_install_fails(tmp_path: Path) -> None:
    """Shell installer should not remove legacy PATH wiring before the new tool install succeeds."""

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "install.sh"
    home_dir = tmp_path / "home"
    user_bin_dir = tmp_path / "bin"
    home_dir.mkdir(parents=True, exist_ok=True)
    user_bin_dir.mkdir(parents=True, exist_ok=True)
    legacy_profile = home_dir / ".zprofile"
    legacy_profile.write_text(
        "# >>> AFKBOT PATH >>>\nexport PATH=\"/legacy/afk:$PATH\"\n# <<< AFKBOT PATH <<<\n",
        encoding="utf-8",
    )
    uv_log = tmp_path / "uv-install.log"
    _write_executable(
        user_bin_dir / "uv",
        f"""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "{uv_log}"
if [[ "${{1:-}}" == "tool" && "${{2:-}}" == "install" ]]; then
  exit 42
fi
if [[ "${{1:-}}" == "tool" && "${{2:-}}" == "update-shell" ]]; then
  exit 0
fi
if [[ "${{1:-}}" == "tool" && "${{2:-}}" == "dir" && "${{3:-}}" == "--bin" ]]; then
  printf '%s\\n' "{user_bin_dir}"
  exit 0
fi
exit 0
""",
    )

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["XDG_BIN_HOME"] = str(user_bin_dir)

    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--repo-url",
            f"file://{repo_root}",
            "--git-ref",
            "local-failure",
            "--skip-setup",
        ],
        capture_output=True,
        check=False,
        cwd=repo_root,
        env=env,
        text=True,
    )

    assert result.returncode != 0
    assert "# >>> AFKBOT PATH >>>" in legacy_profile.read_text(encoding="utf-8")
    assert "tool install" in uv_log.read_text(encoding="utf-8")


def test_shell_uninstaller_continues_legacy_cleanup_when_uv_tool_is_missing(tmp_path: Path) -> None:
    """Shell uninstaller should keep cleaning legacy state when uv tool uninstall fails."""

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "uninstall.sh"
    home_dir = tmp_path / "home"
    user_bin_dir = tmp_path / "bin"
    home_dir.mkdir(parents=True, exist_ok=True)
    user_bin_dir.mkdir(parents=True, exist_ok=True)
    legacy_profile = home_dir / ".zprofile"
    legacy_profile.write_text(
        "# >>> AFKBOT PATH >>>\nexport PATH=\"/legacy/afk:$PATH\"\n# <<< AFKBOT PATH <<<\n",
        encoding="utf-8",
    )
    legacy_install_dir = _legacy_unix_install_dir(home_dir)
    (legacy_install_dir / "bin").mkdir(parents=True, exist_ok=True)
    (legacy_install_dir / "bin" / "afk").write_text("#!/bin/sh\n", encoding="utf-8")
    legacy_alias = home_dir / ".local" / "bin" / "afk"
    legacy_alias.parent.mkdir(parents=True, exist_ok=True)
    legacy_alias.symlink_to(legacy_install_dir / "bin" / "afk")
    uv_log = tmp_path / "uv-uninstall.log"
    _write_executable(
        user_bin_dir / "uv",
        f"""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "{uv_log}"
if [[ "${{1:-}}" == "tool" && "${{2:-}}" == "dir" && "${{3:-}}" == "--bin" ]]; then
  printf '%s\\n' "{user_bin_dir}"
  exit 0
fi
if [[ "${{1:-}}" == "tool" && "${{2:-}}" == "uninstall" ]]; then
  exit 2
fi
exit 0
""",
    )

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["XDG_BIN_HOME"] = str(user_bin_dir)

    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--yes",
        ],
        capture_output=True,
        check=False,
        cwd=repo_root,
        env=env,
        text=True,
    )

    assert result.returncode == 0
    assert "# >>> AFKBOT PATH >>>" not in legacy_profile.read_text(encoding="utf-8")
    assert not legacy_alias.exists()
    assert not legacy_alias.is_symlink()
    assert not legacy_install_dir.exists()
    assert "tool uninstall afkbotio" in uv_log.read_text(encoding="utf-8")


def test_render_windows_launcher_uses_single_newlines_before_windows_translation() -> None:
    """Windows launcher rendering should avoid embedded carriage returns before file write."""

    # Arrange
    runtime_relative = "..\\runtime"
    metadata_relative = "..\\managed-install.json"
    python_relative = "..\\venv\\Scripts\\python.exe"

    # Act
    launcher_content = _render_windows_launcher(
        runtime_relative=runtime_relative,
        metadata_relative=metadata_relative,
        python_relative=python_relative,
    )

    # Assert
    assert "\r" not in launcher_content
    assert f'set "{MANAGED_RUNTIME_DIR_ENV}=%~dp0{runtime_relative}"' in launcher_content
    assert f'set "{MANAGED_METADATA_PATH_ENV}=%~dp0{metadata_relative}"' in launcher_content


def test_safe_extract_tar_rejects_parent_path_escape(tmp_path: Path) -> None:
    """Managed archive extraction should reject entries that escape the destination root."""

    # Arrange
    archive_path = tmp_path / "unsafe-path.tar.gz"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"owned"
        member = tarfile.TarInfo("../escape.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    # Act
    with tarfile.open(archive_path, "r:gz") as archive:
        with pytest.raises(ValueError, match=r"unsafe path: \.\./escape\.txt"):
            _safe_extract_tar(archive=archive, destination=extract_dir)

    # Assert
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_tar_rejects_symlink_escape(tmp_path: Path) -> None:
    """Managed archive extraction should reject symlinks that point outside the destination."""

    # Arrange
    archive_path = tmp_path / "unsafe-link.tar.gz"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        link = tarfile.TarInfo("link-out")
        link.type = tarfile.SYMTYPE
        link.linkname = "../escape"
        archive.addfile(link)

    # Act
    with tarfile.open(archive_path, "r:gz") as archive:
        with pytest.raises(ValueError, match=r"unsafe path: link-out"):
            _safe_extract_tar(archive=archive, destination=extract_dir)

    # Assert
    assert not any(extract_dir.iterdir())
